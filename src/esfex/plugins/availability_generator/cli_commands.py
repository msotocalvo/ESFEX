"""CLI sub-commands for the availability_generator plugin.

Registered as ``esfex availability_generator generate ...``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import typer

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="availability_generator",
    help="Generate hourly availability profiles for renewable generators from weather data.",
)


@app.command("generate")
def generate(
    config: Path = typer.Option(
        ..., "--config", "-c", exists=True, help="Path to ESFEX YAML config.",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output directory (default: ./availability/).",
    ),
    data_source: str = typer.Option(
        "open_meteo", "--source", "-s",
        help="Data source: open_meteo, nasa_power, era5_atlite.",
    ),
    years_str: Optional[str] = typer.Option(
        None, "--years", "-y",
        help="Comma-separated years (e.g. 2020,2021). Default: from config.",
    ),
    generators_str: Optional[str] = typer.Option(
        None, "--generators", "-g",
        help="Comma-separated generator keys to process. Default: all renewable.",
    ),
    update_config: bool = typer.Option(
        False, "--update-config",
        help="Update the YAML config with generated availability_file paths.",
    ),
    # Solar parameters
    tilt: Optional[float] = typer.Option(
        None, "--tilt", help="Panel tilt in degrees (default: latitude-optimal).",
    ),
    azimuth: float = typer.Option(
        180.0, "--azimuth", help="Panel azimuth in degrees (180 = south).",
    ),
    tracking: str = typer.Option(
        "none", "--tracking",
        help="Tracking mode: none, horizontal, vertical, dual.",
    ),
    efficiency: float = typer.Option(
        0.20, "--efficiency", help="Module STC efficiency (0-1).",
    ),
    # Wind parameters
    turbine: Optional[str] = typer.Option(
        None, "--turbine", help="Turbine key from atlite database.",
    ),
    hub_height: int = typer.Option(
        80, "--hub-height", help="Hub height in meters.",
    ),
) -> None:
    """Generate hourly availability CSV profiles for renewable generators."""
    from esfex.config.loader import load_config

    typer.echo(f"Loading config: {config}")
    cfg = load_config(config)

    # Resolve output directory
    out_dir = output or (config.parent / "availability")

    # Resolve years
    if years_str:
        years = [int(y.strip()) for y in years_str.split(",")]
    else:
        years = _default_years(cfg)

    # Resolve generator filter
    gen_filter = None
    if generators_str:
        gen_filter = [g.strip() for g in generators_str.split(",")]

    # Build parameter dicts
    solar_params = {
        "efficiency": efficiency,
        "gamma_pmax": -0.40,
        "t_noct": 45.0,
        "tilt": tilt,
        "azimuth": azimuth,
        "tracking": tracking,
    }
    wind_params = {
        "turbine_key": turbine,
        "hub_height": hub_height,
    }

    typer.echo(
        f"Generating profiles for {len(years)} year(s) "
        f"using {data_source}..."
    )

    from .generator import generate_availability_profiles, update_config_yaml

    def progress_cb(pct: int, msg: str) -> None:
        typer.echo(f"  [{pct:3d}%] {msg}")

    result_map = generate_availability_profiles(
        config=cfg,
        years=years,
        output_dir=out_dir,
        data_source=data_source,
        solar_params=solar_params,
        wind_params=wind_params,
        generator_filter=gen_filter,
        progress_callback=progress_cb,
    )

    if not result_map:
        typer.echo("No profiles generated (no renewable generators with coordinates found).")
        raise typer.Exit(1)

    typer.echo(f"\nGenerated {len(result_map)} availability profile(s):")
    for key, path in sorted(result_map.items()):
        typer.echo(f"  {key} -> {path}")

    if update_config:
        update_config_yaml(config, result_map)
        typer.echo(f"\nUpdated config: {config}")


def _default_years(cfg) -> list[int]:
    """Extract default year(s) from config temporal settings."""
    try:
        temporal = cfg.temporal
        start = getattr(temporal, "date_start", None)
        if start:
            if hasattr(start, "year"):
                return [start.year]
            # May be a string like "2020-01-01"
            return [int(str(start)[:4])]
    except Exception:
        pass
    return [2020]
