"""Orchestrator for availability profile generation.

Scans an ESFEX config for renewable generators, computes hourly capacity
factor time series using weather data, and saves CSV files.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from solarex import compute_solar_hourly_cf
from windrex import compute_wind_hourly_cf

logger = logging.getLogger(__name__)

_HOURS_PER_YEAR = 8760
_SOLAR_FUEL_HINTS = {"solar", "pv", "fotovoltaic", "photovoltaic", "sun"}
_WIND_FUEL_HINTS = {"wind", "eolic", "eólic", "turbine", "aerogenerador"}


def _guess_profile_type(fuel: str) -> str:
    """Guess 'Solar' or 'Wind' from fuel name; return '' if unknown."""
    lower = fuel.lower()
    for hint in _SOLAR_FUEL_HINTS:
        if hint in lower:
            return "Solar"
    for hint in _WIND_FUEL_HINTS:
        if hint in lower:
            return "Wind"
    return ""


def generate_availability_profiles(
    config: Any,
    years: list[int],
    output_dir: Path,
    data_source: str = "open_meteo",
    solar_params: Optional[dict] = None,
    wind_params: Optional[dict] = None,
    generator_filter: Optional[list[str]] = None,
    profile_type_map: Optional[dict[str, str]] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> dict[str, Path]:
    """Generate hourly availability CSVs for all renewable generators.

    Parameters
    ----------
    config : ESFEXConfig
        Loaded ESFEX configuration.
    years : list[int]
        Calendar years to generate profiles for.
    output_dir : Path
        Directory where CSV files are saved.
    data_source : str
        Weather data backend: ``"open_meteo"``, ``"nasa_power"``,
        ``"era5_atlite"``.
    solar_params : dict or None
        Solar PV parameters: efficiency, gamma_pmax, t_noct, tilt, azimuth,
        tracking.
    wind_params : dict or None
        Wind parameters: turbine_key, hub_height, wind_speeds, power_curve,
        rated_power_mw.
    generator_filter : list[str] or None
        If given, only process these generator keys.
    profile_type_map : dict[str, str] or None
        Explicit mapping of ``"system_name/gen_key"`` to ``"Solar"`` or
        ``"Wind"``.  When provided (GUI mode), overrides fuel-name heuristics.
        When *None* (CLI mode), profile type is guessed from the fuel name.
    progress_callback : callable or None
        ``callback(percent, message)`` for progress updates.

    Returns
    -------
    dict[str, Path]
        Mapping of ``"system_name/gen_key"`` to output CSV file path.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    solar_params = solar_params or {}
    wind_params = wind_params or {}

    # Collect all renewable generators across systems
    tasks = _collect_tasks(config, generator_filter, profile_type_map)

    if not tasks:
        _progress(progress_callback, 100, "No renewable generators found.")
        return {}

    total_steps = len(tasks) * len(years)
    step = 0
    result_map: dict[str, Path] = {}

    for sys_name, gen_key, gen, coords, profile_type in tasks:
        num_nodes = len(gen.rated_power)
        year_profiles: list[np.ndarray] = []

        for year in years:
            step += 1
            pct = int(100 * step / total_steps)

            # Build per-node CF for this year
            node_cfs: list[np.ndarray] = []

            for node_idx in range(num_nodes):
                # Skip nodes without capacity or investment potential
                has_capacity = gen.rated_power[node_idx] > 0
                has_invest = (
                    node_idx < len(gen.invest_max_power)
                    and gen.invest_max_power[node_idx] > 0
                )
                if not has_capacity and not has_invest:
                    node_cfs.append(np.ones(_HOURS_PER_YEAR))
                    continue

                lat, lon = coords[node_idx]

                _progress(
                    progress_callback, pct,
                    f"Fetching {data_source} data for {gen_key} "
                    f"node {node_idx} ({lat:.2f}, {lon:.2f}) year {year}...",
                )

                if profile_type == "Solar":
                    cf = compute_solar_hourly_cf(
                        lat, lon, year, data_source,
                        efficiency=solar_params.get("efficiency", 0.20),
                        gamma_pmax=solar_params.get("gamma_pmax", -0.40),
                        t_noct=solar_params.get("t_noct", 45.0),
                        tilt=solar_params.get("tilt"),
                        azimuth=solar_params.get("azimuth", 180.0),
                        tracking=solar_params.get("tracking", "none"),
                    )
                elif profile_type == "Wind":
                    cf = compute_wind_hourly_cf(
                        lat, lon, year, data_source,
                        wind_speeds=wind_params.get("wind_speeds"),
                        power_curve=wind_params.get("power_curve"),
                        rated_power_mw=wind_params.get("rated_power_mw", 3.0),
                        hub_height=wind_params.get("hub_height", 80),
                        turbine_key=wind_params.get("turbine_key"),
                    )
                else:
                    cf = np.ones(_HOURS_PER_YEAR)

                node_cfs.append(cf)

            # Stack nodes into (8760, num_nodes) array
            year_array = np.column_stack(node_cfs)
            year_profiles.append(year_array)

        # Concatenate years vertically: (8760*Y, num_nodes)
        full_profile = np.vstack(year_profiles)

        # Save CSV
        csv_name = f"{gen_key}_availability.csv"
        if len(config.systems) > 1:
            sys_dir = output_dir / sys_name
            sys_dir.mkdir(parents=True, exist_ok=True)
            csv_path = sys_dir / csv_name
        else:
            csv_path = output_dir / csv_name

        np.savetxt(csv_path, full_profile, delimiter=",", fmt="%.6f")

        map_key = f"{sys_name}/{gen_key}"
        result_map[map_key] = csv_path
        logger.info(
            "Saved %s: shape %s, mean CF %.3f",
            csv_path, full_profile.shape, float(full_profile.mean()),
        )

    _progress(progress_callback, 100, f"Done. Generated {len(result_map)} profile(s).")
    return result_map


def _collect_tasks(
    config: Any,
    generator_filter: Optional[list[str]],
    profile_type_map: Optional[dict[str, str]] = None,
) -> list[tuple[str, str, Any, list[tuple[float, float]], str]]:
    """Scan config for renewable generators with node coordinates.

    Returns list of ``(system_name, gen_key, gen_config, node_coords,
    profile_type)`` where *profile_type* is ``"Solar"`` or ``"Wind"``.
    """
    tasks = []

    for sys_name, system in config.systems.items():
        # Get node coordinates
        node_coords = _get_node_coordinates(system)
        if node_coords is None:
            logger.warning(
                "System '%s': no node_coordinates set, skipping. "
                "Please add node_coordinates to the config or place "
                "nodes on the map in the Studio.",
                sys_name,
            )
            continue

        for gen_key, gen in system.generators.items():
            if generator_filter and gen_key not in generator_filter:
                continue

            # Only renewable generators
            if gen.type != "Renewable":
                continue

            # Determine profile type
            full_key = f"{sys_name}/{gen_key}"
            if profile_type_map and full_key in profile_type_map:
                # Explicit mapping from GUI
                pt = profile_type_map[full_key]
            else:
                # CLI fallback: guess from fuel name
                pt = _guess_profile_type(gen.fuel)

            if pt not in ("Solar", "Wind"):
                logger.info(
                    "System '%s' gen '%s': fuel '%s' not recognized as "
                    "Solar or Wind, skipping.",
                    sys_name, gen_key, gen.fuel,
                )
                continue

            num_nodes = len(gen.rated_power)
            if len(node_coords) < num_nodes:
                logger.warning(
                    "System '%s' gen '%s': %d nodes but only %d coordinates, "
                    "skipping.",
                    sys_name, gen_key, num_nodes, len(node_coords),
                )
                continue

            tasks.append((sys_name, gen_key, gen, node_coords[:num_nodes], pt))

    return tasks


def _get_node_coordinates(system: Any) -> Optional[list[tuple[float, float]]]:
    """Extract (lat, lon) pairs from system's node_coordinates."""
    coords = getattr(system.nodes, "node_coordinates", None)
    if coords is None:
        return None
    return [(c.latitude, c.longitude) for c in coords]


def _progress(
    callback: Optional[Callable[[int, str], None]],
    pct: int,
    msg: str,
) -> None:
    """Emit progress if callback is set."""
    if callback is not None:
        callback(pct, msg)


def update_config_yaml(
    config_path: Path,
    gen_file_map: dict[str, Path],
) -> None:
    """Patch a YAML config file to set availability_file paths.

    Parameters
    ----------
    config_path : Path
        Path to the ESFEX YAML configuration file.
    gen_file_map : dict[str, Path]
        Mapping of ``"system_name/gen_key"`` to CSV file path.
    """
    try:
        from ruamel.yaml import YAML
        # `typ='safe'` refuses `!!python/object` and other tags that
        # would otherwise instantiate arbitrary objects from the YAML —
        # i.e. RCE on `yaml.load(config_path)` against a malicious file.
        # `preserve_quotes` is incompatible with the safe loader, but we
        # only need it for nicer round-trip on dump; correctness wins.
        yaml = YAML(typ='safe')
    except ImportError:
        import yaml as pyyaml  # type: ignore[no-redef]
        logger.warning(
            "ruamel.yaml not installed; using PyYAML (comments may be lost)."
        )

        text = config_path.read_text(encoding="utf-8")
        data = pyyaml.safe_load(text)

        for full_key, csv_path in gen_file_map.items():
            sys_name, gen_key = full_key.split("/", 1)
            rel_path = _relative_path(config_path, csv_path)
            systems = data.get("systems", {})
            sys_block = systems.get(sys_name, {})
            gens = sys_block.get("generators", {})
            gen_block = gens.get(gen_key, {})
            gen_block["Availability"] = str(rel_path)

        with config_path.open("w", encoding="utf-8") as f:
            pyyaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        return

    # ruamel.yaml path
    data = yaml.load(config_path)

    for full_key, csv_path in gen_file_map.items():
        sys_name, gen_key = full_key.split("/", 1)
        rel_path = _relative_path(config_path, csv_path)
        try:
            data["systems"][sys_name]["generators"][gen_key]["Availability"] = str(
                rel_path
            )
        except (KeyError, TypeError):
            logger.warning("Could not update path for %s in YAML.", full_key)

    yaml.dump(data, config_path)
    logger.info("Updated %s with availability file paths.", config_path)


def _relative_path(config_path: Path, csv_path: Path) -> Path:
    """Compute relative path from config directory to CSV file."""
    try:
        return csv_path.resolve().relative_to(config_path.parent.resolve())
    except ValueError:
        return csv_path.resolve()
