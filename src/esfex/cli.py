"""
Command-line interface for ESFEX.

Provides commands for running optimization, validating configuration,
and exporting results.
"""

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

app = typer.Typer(
    name="esfex",
    help="ESFEX: Energy System FlEXibility — Power System Optimization",
    add_completion=False,
)
# Force UTF-8 output to avoid cp1252 UnicodeEncodeError on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _force_blocking_stdio() -> None:
    """Force stdout/stderr file descriptors to blocking mode (POSIX only).

    Background: when ESFEX is launched as a subprocess by the GUI
    (``QProcess`` in python_console.py) and juliacall is initialised
    during the run, Julia's stdio setup leaves the parent Python's
    stdout/stderr file descriptors in ``O_NONBLOCK`` mode. Once that
    happens, a Rich Console write that doesn't fit in the pipe buffer
    raises ``BlockingIOError([Errno 11])`` instead of blocking until
    the GUI drains the pipe. The error then cascades through
    ``Progress.__exit__`` and the typer except hook, producing a
    hundreds-of-lines log spam and obscuring the real outcome.

    Reapplying blocking mode here, before any heavy work runs, removes
    the cascade at the root. Best-effort: silent no-op on Windows or
    when fcntl isn't available, or on fds that don't support it
    (already-redirected to a non-pipe).
    """
    try:
        import fcntl, os
    except ImportError:
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            fd = stream.fileno()
        except (AttributeError, OSError, ValueError):
            continue
        try:
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            if flags & os.O_NONBLOCK:
                fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
        except (OSError, ValueError):
            # Some fds (e.g. closed, non-seekable) reject F_SETFL.
            # Nothing useful to do — the cascade-handling fallback in
            # the except blocks below will catch BlockingIOError if it
            # still happens.
            continue


_force_blocking_stdio()

# Trim tracebacks. The default rich excepthook installed by typer
# dumps every local frame variable; when the locals include a
# ``ESFEXConfig`` object (~1000 fields, deeply nested), the output
# balloons to thousands of lines and on a slow pipe contributes to
# the BlockingIOError cascade _force_blocking_stdio fights against.
# ``show_locals=False`` keeps the frame chain readable; ``max_frames``
# caps the depth so even a recursive bug doesn't explode the output.
try:
    from rich.traceback import install as _install_rich_tb
    _install_rich_tb(show_locals=False, max_frames=10)
except Exception:
    # rich.traceback is best-effort presentation; never block startup
    # because the prettifier itself failed.
    pass

console = Console(force_terminal=True)


@app.command()
def run(
    config: Path = typer.Option(
        ...,
        "--config",
        "-c",
        help="Path to configuration YAML file",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    mode: Optional[str] = typer.Option(
        None,
        "--mode",
        "-m",
        help="Simulation mode: 'development' or 'unit_commitment'. "
             "If omitted, the value from the config file is used.",
    ),
    solver: Optional[str] = typer.Option(
        None,
        "--solver",
        "-s",
        help="Override solver from config: 'highs', 'cbc', 'glpk', 'gurobi', 'cplex'",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory for results (default: ./results)",
    ),
    years: Optional[int] = typer.Option(
        None,
        "--years",
        "-y",
        help="Number of years to simulate (default: from config)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose output",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate config and show plan without running optimization",
    ),
):
    """
    Run the ESFEX optimization model.

    Executes capacity expansion and operational dispatch optimization
    based on the provided configuration file.
    """
    import logging
    import sys

    from esfex.config.loader import load_config, ConfigLoadError
    from esfex.runner import Orchestrator
    from esfex.logging_config import setup_console_logging

    # Install the (single) console handler at the right verbosity.
    # When the user passed --verbose, lock the level to debug so the
    # later runner-internal call doesn't reset it. Without --verbose,
    # we start at the default "basic" and let cfg.logging.console_level
    # (and then runner) refine it.
    setup_console_logging(level="debug" if verbose else "basic", force=verbose)

    if verbose:
        console.print(f"[bold blue]ESFEX[/bold blue] - Power System Optimization")
        console.print(f"Configuration: {config}")
        console.print(f"Mode: {mode}")
        console.print(f"Solver: {solver or '(from config)'}")
        console.print()

    # Load and validate configuration
    try:
        with Progress(
            SpinnerColumn("line"),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task(description="Loading configuration...", total=None)
            cfg = load_config(config)
    except ConfigLoadError as e:
        console.print(f"[red]Configuration error:[/red] {e}")
        raise typer.Exit(code=1)

    # Override config values from CLI
    if mode:
        cfg.simulation_mode = mode
    if solver:
        cfg.solver.name = solver
    if verbose:
        cfg.solver.verbose = True

    # Re-tune console logging once the yaml's `logging.console_level`
    # is available. --verbose on the CLI always wins (debug everywhere)
    # so the user can override what the yaml says without editing it.
    if not verbose:
        setup_console_logging(level=cfg.logging.console_level)

    # Align the solver's own verbosity with the console mode the user
    # picked.  Without this, a yaml shipped with ``solver.verbose: true``
    # would still flood a "basic" console with Gurobi simplex
    # iterations — defeating the whole point of choosing basic.
    #
    # Policy:
    #   basic   → force solver silent (no Gurobi log)
    #   debug   → force solver verbose (gives the developer full
    #             diagnostic output that pairs with debug logging)
    #   verbose → leave whatever the yaml said; the user is explicitly
    #             asking for an in-between mode and the per-yaml flag
    #             is the natural escape hatch
    if not verbose:
        _cl = cfg.logging.console_level
        if _cl == "basic":
            cfg.solver.verbose = False
        elif _cl == "debug":
            cfg.solver.verbose = True

    # Show configuration summary (only in verbose mode)
    if verbose:
        _show_config_summary(cfg)

    if dry_run:
        console.print("\n[yellow]Dry run mode - not executing optimization[/yellow]")
        raise typer.Exit(code=0)

    # Create output directory
    output_dir = output or Path("./results")
    output_dir.mkdir(parents=True, exist_ok=True)

    def _safe_console_print(msg: str, *, style: str = "") -> None:
        """Print via rich, but if stdout is in non-blocking mode and the
        pipe is full (``BlockingIOError``), fall back to a raw fd write
        so the user sees something instead of a hundred-line cascade of
        '--- Logging error ---' messages. Re-applies blocking mode
        on the way out in case more output follows.
        """
        try:
            if style:
                console.print(f"[{style}]{msg}[/{style}]")
            else:
                console.print(msg)
        except BlockingIOError:
            _force_blocking_stdio()
            try:
                sys.__stderr__.write(msg + "\n")
                sys.__stderr__.flush()
            except Exception:
                pass

    # Re-apply blocking mode in case anything since import time (e.g. a
    # juliacall warmup) flipped the fds back into O_NONBLOCK.
    _force_blocking_stdio()

    # Run optimization
    try:
        orchestrator = Orchestrator(cfg, output_dir=output_dir, config_path=config)
        results = orchestrator.run(years=years)

        _safe_console_print("\nOptimization completed successfully!", style="green")
        _safe_console_print(f"Results saved to: {output_dir}")

    except BlockingIOError as e:
        # Stdout pipe was non-blocking and full (typical when GUI
        # subprocess can't drain stdout fast enough). Try once more
        # after re-blocking; otherwise just exit quietly with the code.
        _force_blocking_stdio()
        try:
            sys.__stderr__.write(
                f"\nOptimization aborted: stdout pipe full ({e}). "
                "Increase the GUI console's drain rate or rerun from a "
                "terminal.\n"
            )
            sys.__stderr__.flush()
        except Exception:
            pass
        raise typer.Exit(code=1)
    except Exception as e:
        _force_blocking_stdio()
        _safe_console_print(f"\nOptimization failed: {e}", style="red")
        if verbose:
            import traceback
            _safe_console_print(traceback.format_exc())
        raise typer.Exit(code=1)


@app.command()
def validate(
    config: Path = typer.Option(
        ...,
        "--config",
        "-c",
        help="Path to configuration YAML file",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
):
    """
    Validate a configuration file.

    Checks that the configuration is valid and all required files exist.
    """
    from esfex.config.loader import load_config, ConfigLoadError

    console.print(f"Validating: {config}")

    try:
        cfg = load_config(config)
        console.print("[green]Configuration is valid![/green]")
        _show_config_summary(cfg)

    except ConfigLoadError as e:
        console.print(f"[red]Validation failed:[/red]\n{e}")
        raise typer.Exit(code=1)


@app.command()
def export(
    results: Path = typer.Option(
        ...,
        "--results",
        "-r",
        help="Path to HDF5 results file",
        exists=True,
    ),
    format: str = typer.Option(
        "csv",
        "--format",
        "-f",
        help="Export format: 'csv', 'excel', or 'json'",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory for exported files",
    ),
):
    """
    Export results to different formats.

    Converts HDF5 results to CSV, Excel, or JSON.
    """
    console.print(f"Exporting results from: {results}")
    console.print(f"Format: {format}")

    output_dir = output or results.parent / "export"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from esfex.io.exporter import ResultsExporter

        exporter = ResultsExporter(results)

        if format.lower() == "csv":
            exporter.to_csv(output_dir)
        elif format.lower() == "excel":
            exporter.to_excel(output_dir / f"{results.stem}.xlsx")
        elif format.lower() == "json":
            exporter.to_json(output_dir / f"{results.stem}.json")
        else:
            console.print(f"[red]Unknown format: {format}[/red]")
            raise typer.Exit(code=1)

        console.print(f"[green]Export completed![/green]")
        console.print(f"Output: {output_dir}")

    except Exception as e:
        console.print(f"[red]Export failed:[/red] {e}")
        raise typer.Exit(code=1)


@app.command()
def studio(
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to existing YAML config to edit",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output YAML path (default: overwrites input)",
    ),
):
    """
    Launch the GIS-based power system designer (Studio).

    Opens an interactive map-based GUI for creating and editing
    the power system configuration visually.
    """
    try:
        from esfex.visualization import launch_studio
    except ImportError:
        console.print(
            "[red]PySide6 is required for the Studio.[/red]\n"
            "It ships with esfex; reinstall with: "
            "pip install --upgrade --force-reinstall esfex"
        )
        raise typer.Exit(code=1)

    console.print("[bold blue]ESFEX[/bold blue] - Studio")
    if config:
        console.print(f"Loading: {config}")

    result = launch_studio(config=str(config) if config else None, blocking=True)

    if result:
        out_path = output or config or Path("esfex_config.yaml")
        console.print(f"[green]Configuration saved to: {out_path}[/green]")
    else:
        console.print("[yellow]Studio closed without saving.[/yellow]")


# ── Plugin management sub-commands ────────────────────────────────────

plugin_app = typer.Typer(
    name="plugin",
    help="Manage ESFEX plugins (install, enable, disable, list).",
)
app.add_typer(plugin_app, name="plugin")


@plugin_app.command("list")
def plugin_list():
    """List all discovered plugins and their status."""
    from esfex.plugins import get_plugin_manager

    pm = get_plugin_manager()
    names = pm.discover()

    if not names:
        console.print("[yellow]No plugins found.[/yellow]")
        console.print(
            "Plugins are discovered from:\n"
            "  ~/.esfex/plugins/\n"
            "  <project>/.esfex/plugins/\n"
            "  $ESFEX_PLUGIN_PATH"
        )
        return

    table = Table(title="ESFEX Plugins")
    table.add_column("Name", style="cyan")
    table.add_column("Version", style="green")
    table.add_column("Category", style="blue")
    table.add_column("Status", style="bold")
    table.add_column("Description")

    for name in sorted(names):
        meta = pm.metas.get(name)
        if meta is None:
            continue
        enabled = pm.is_enabled(name)
        status = "[green]enabled[/green]" if enabled else "[red]disabled[/red]"
        table.add_row(name, meta.version, meta.category, status, meta.description)

    console.print(table)


@plugin_app.command("install")
def plugin_install(
    git: Optional[str] = typer.Option(None, "--git", help="Git URL to clone"),
    zip_file: Optional[Path] = typer.Option(
        None, "--zip", help="ZIP file to extract", exists=True
    ),
    name: Optional[str] = typer.Option(
        None, "--name", "-n", help="Target directory name (for git)"
    ),
):
    """Install a plugin from a git repository or ZIP file."""
    from esfex.plugins import get_plugin_manager

    pm = get_plugin_manager()

    if git:
        try:
            installed = pm.install_from_git(git, target_name=name)
            console.print(f"[green]Installed plugin:[/green] {installed}")
        except Exception as e:
            console.print(f"[red]Install failed:[/red] {e}")
            raise typer.Exit(code=1)
    elif zip_file:
        try:
            installed = pm.install_from_zip(zip_file)
            console.print(f"[green]Installed plugin:[/green] {installed}")
        except Exception as e:
            console.print(f"[red]Install failed:[/red] {e}")
            raise typer.Exit(code=1)
    else:
        console.print("[red]Provide --git <url> or --zip <path>[/red]")
        raise typer.Exit(code=1)


@plugin_app.command("uninstall")
def plugin_uninstall(
    name: str = typer.Argument(..., help="Plugin name to uninstall"),
):
    """Uninstall a plugin by removing its directory."""
    from esfex.plugins import get_plugin_manager

    pm = get_plugin_manager()
    pm.uninstall(name)
    console.print(f"[green]Uninstalled plugin:[/green] {name}")


@plugin_app.command("enable")
def plugin_enable(
    name: str = typer.Argument(..., help="Plugin name to enable"),
):
    """Enable a disabled plugin."""
    from esfex.plugins import get_plugin_manager

    get_plugin_manager().enable(name)
    console.print(f"[green]Enabled plugin:[/green] {name}")


@plugin_app.command("disable")
def plugin_disable(
    name: str = typer.Argument(..., help="Plugin name to disable"),
):
    """Disable a plugin without uninstalling it."""
    from esfex.plugins import get_plugin_manager

    get_plugin_manager().disable(name)
    console.print(f"[yellow]Disabled plugin:[/yellow] {name}")


@app.command()
def precompile(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Rebuild sysimage even if one already exists",
    ),
):
    """
    Build a Julia sysimage for faster simulation startup.

    Creates a native sysimage containing JuMP, HiGHS, and the ESFEX
    optimization pipeline pre-compiled into machine code. This is a
    one-time operation (~5-10 minutes) that reduces simulation startup
    from ~30s to <3s.

    The sysimage is automatically detected on subsequent runs.
    Rebuild with --force after modifying Julia source files.
    """
    from esfex.bridge.julia_setup import (
        _find_sysimage,
        _sysimage_is_stale,
        precompile_esfex,
    )

    # Check current state
    existing = _find_sysimage()
    if existing and not force and not _sysimage_is_stale(existing):
        console.print(f"[green]Sysimage is up to date:[/green] {existing}")
        console.print("Use --force to rebuild.")
        return

    if existing and _sysimage_is_stale(existing):
        console.print("[yellow]Sysimage is stale — rebuilding...[/yellow]")
    else:
        console.print("[bold]Building Julia sysimage (this may take 5-10 minutes)...[/bold]")

    path = precompile_esfex(force=force)

    if path:
        console.print(f"\n[green]Sysimage built successfully:[/green] {path}")
        size_mb = path.stat().st_size / (1024 * 1024)
        console.print(f"Size: {size_mb:.0f} MB")
        console.print("Subsequent simulations will start ~10-20x faster.")
    else:
        console.print("\n[red]Sysimage build failed.[/red]")
        console.print("Make sure Julia is installed and available in PATH.")
        raise typer.Exit(code=1)


@app.command()
def info():
    """
    Show ESFEX version and system information.
    """
    import sys
    from esfex import __version__

    console.print(f"[bold blue]ESFEX[/bold blue] version {__version__}")
    console.print(f"Python: {sys.version}")

    # Check for Julia
    julia_available = False
    try:
        from juliacall import Main as jl
        console.print(f"Julia: [green]Available[/green] via juliacall")
        julia_available = True
    except ImportError:
        console.print(f"Julia: [yellow]Not available (juliacall not installed)[/yellow]")

    # Sysimage status
    from esfex.bridge.julia_setup import _find_sysimage, _sysimage_is_stale

    sysimage = _find_sysimage()
    if sysimage:
        stale = _sysimage_is_stale(sysimage)
        size_mb = sysimage.stat().st_size / (1024 * 1024)
        if stale:
            console.print(
                f"Sysimage: [yellow]STALE[/yellow] ({sysimage}, {size_mb:.0f} MB)"
            )
            console.print("  Run `esfex precompile --force` to rebuild")
        else:
            console.print(
                f"Sysimage: [green]UP TO DATE[/green] ({sysimage}, {size_mb:.0f} MB)"
            )
    else:
        console.print("Sysimage: [yellow]Not built[/yellow]")
        console.print("  Run `esfex precompile` for faster simulation startup")

    # Check for solvers (via Julia/JuMP)
    console.print("\n[bold]Available solvers (Julia/JuMP):[/bold]")
    if julia_available:
        _check_julia_solver("HiGHS")
        _check_julia_solver("Gurobi")
        _check_julia_solver("CPLEX")
    else:
        console.print("  [yellow]Julia not available - cannot check solvers[/yellow]")


def _check_julia_solver(name: str):
    """Check if a solver is available in Julia."""
    try:
        from esfex.config.solver import get_solver_info
        info = get_solver_info(name)
        if info["available"]:
            version_str = f" (v{info['version']})" if info.get("version") else ""
            console.print(f"  {name}: [green]Available{version_str}[/green]")
        else:
            console.print(f"  {name}: [yellow]Not found[/yellow]")
    except Exception as e:
        console.print(f"  {name}: [red]Error checking[/red]")


def _show_config_summary(cfg):
    """Display a summary of the configuration."""
    from esfex.config.schema import ESFEXConfig

    table = Table(title="Configuration Summary")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Simulation Mode", cfg.simulation_mode)
    table.add_row("Solver", cfg.solver.name)
    table.add_row("Systems", ", ".join(cfg.meta_network.systems))

    for sys_name in cfg.meta_network.systems:
        sys_cfg = cfg.systems[sys_name]
        table.add_row(f"  {sys_name} nodes", str(sys_cfg.num_nodes))
        table.add_row(f"  {sys_name} generators", str(len(sys_cfg.generators)))
        table.add_row(f"  {sys_name} batteries", str(len(sys_cfg.batteries)))

    table.add_row("Rolling Horizon", str(cfg.temporal.use_rolling_horizon))
    table.add_row("Primary Energy", str(cfg.enable_primary_energy))
    table.add_row("N-1 Security", str(cfg.n1_security.enabled))

    console.print(table)


def _register_plugin_cli() -> None:
    """Discover plugins and register their CLI sub-commands."""
    try:
        from esfex.plugins import get_plugin_manager

        pm = get_plugin_manager()
        pm.discover()
        pm.register_cli_commands(app)
    except Exception:
        pass  # Don't crash the CLI if plugin discovery fails


_register_plugin_cli()


@app.command()
def train_demand_model(
    output: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Output path for trained model (default: ~/.cache/esfex/models/demand_model.xgb)",
    ),
    cache_dir: Optional[Path] = typer.Option(
        None, "--cache-dir",
        help="Directory for caching training data downloads",
    ),
    countries: int = typer.Option(
        200, "--countries", "-n",
        help="Number of countries to include in training data",
    ),
    year_start: int = typer.Option(1990, "--year-start", help="Start year"),
    year_end: int = typer.Option(2023, "--year-end", help="End year"),
):
    """Train the demand estimation ML model from World Bank + ERA5 data.

    Downloads macroeconomic indicators and hourly temperature data for
    ~200 countries, then trains an XGBoost model that predicts 3-hourly
    demand shape factors.  The trained model is used automatically by
    the Demand Estimation workflow.

    First run downloads ~500MB of data (cached for subsequent runs).
    Training takes ~5-10 minutes on a modern CPU.
    """
    from esfex.models.demand_training import train_demand_model as _train

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Training demand model...", total=None)

        def on_progress(pct: int, msg: str) -> None:
            progress.update(task, description=f"[{pct}%] {msg}")

        try:
            model = _train(
                output_path=output,
                cache_dir=cache_dir,
                n_countries=countries,
                year_start=year_start,
                year_end=year_end,
                progress_cb=on_progress,
            )
            console.print(
                f"\n[green]Model trained successfully.[/green] "
                f"Ready for use in Demand Estimation workflow."
            )
        except ImportError as exc:
            console.print(f"\n[red]Missing dependency:[/red] {exc}")
            console.print(
                "xgboost ships with esfex; reinstall with: "
                "pip install --upgrade --force-reinstall esfex"
            )
            raise typer.Exit(code=1)
        except Exception as exc:
            console.print(f"\n[red]Training failed:[/red] {exc}")
            raise typer.Exit(code=1)


@app.command()
def build_demand_dataset(
    sources: Optional[str] = typer.Option(
        "all", "--sources", "-s",
        help="Comma-separated source names: all, opsd, entsoe, brazil, colombia, japan, australia, usa, rte, uk, eskom",
    ),
    cache_dir: Optional[Path] = typer.Option(
        None, "--cache-dir",
        help="Output directory for the dataset",
    ),
):
    """Download and consolidate hourly electricity demand from public sources.

    Downloads real hourly demand data from OPSD (Europe), ENTSO-E, Brazil ONS,
    Colombia XM, TEPCO Japan, AEMO Australia, and more. Also downloads ERA5
    hourly temperature for all country-years. Outputs standardized Parquet files.

    First run downloads 5-50 GB depending on sources. All data is cached.
    """
    from esfex.models.demand_dataset import build_dataset

    source_list = [s.strip() for s in sources.split(",")] if sources else ["all"]

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Building demand dataset...", total=None)

        def on_progress(pct: int, msg: str) -> None:
            progress.update(task, description=f"[{pct}%] {msg}")

        try:
            manifest = build_dataset(
                cache_dir=cache_dir,
                sources=source_list,
                progress_cb=on_progress,
            )
            console.print(
                f"\n[green]Dataset built:[/green] "
                f"{manifest['n_countries']} countries, "
                f"{manifest['n_country_years']} country-years"
            )
        except Exception as exc:
            console.print(f"\n[red]Dataset build failed:[/red] {exc}")
            raise typer.Exit(code=1)


def _entrypoint() -> None:
    """CLI entrypoint (registered in pyproject)."""
    app()


def _studio_entrypoint() -> None:
    """GUI entrypoint for the Start-Menu / desktop launcher.

    Registered as a ``gui-script`` in pyproject so pip builds a
    console-less ``esfex-studio.exe`` (pythonw-based) on Windows:
    double-clicking it opens the Studio with no terminal window.
    Equivalent to ``esfex studio`` with no options — we call
    ``launch_studio`` directly instead of routing through the typer
    ``studio`` command, which would write to a console that doesn't
    exist under pythonw.
    """
    from esfex.visualization import launch_studio

    launch_studio(blocking=True)


if __name__ == "__main__":
    _entrypoint()
