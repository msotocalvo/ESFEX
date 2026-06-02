# Plugin Development Guide

## Overview

ESFEX plugins extend the framework without modifying its core source code. A plugin is a self-contained directory with a `plugin.json` manifest and a Python `__init__.py` entry point. Plugins can:

- **Extend the simulation** --- inject custom logic before/after optimization steps, modify demand data, or add post-processing
- **Extend the GUI** --- add tree categories, property forms, toolbar buttons, menu items, result variables, and map layers
- **Extend the CLI** --- register new subcommands accessible via `esfex <plugin_name> ...`
- **Inject Julia code** --- provide `.jl` overlay modules that are `include()`-d at runtime, adding constraints or variables to the optimization model without altering ESFEX's native Julia files

### Plugin Discovery Mechanism

The `PluginManager` scans three locations in order:

1. **User plugins directory**: `~/.esfex/plugins/`
2. **Project-local directory**: `<project_dir>/.esfex/plugins/`
3. **Environment variable**: directories listed in `$ESFEX_PLUGIN_PATH` (colon-separated on Linux/macOS, semicolon-separated on Windows)

Each subdirectory containing both a `plugin.json` and an `__init__.py` is recognized as a valid plugin. Discovery happens on startup; the first plugin found with a given name wins (duplicates are skipped with a debug log).


---


## Plugin Structure

Minimal directory layout:

```
my_plugin/
    plugin.json        # Metadata manifest (required)
    __init__.py        # Entry point with create_plugin() factory (required)
```

A more complex plugin:

```
carbon_tracker/
    plugin.json
    __init__.py
    tracker.py         # Core logic
    gui_panel.py       # Optional GUI extensions
    cli_commands.py    # Optional CLI subcommands
    julia/
        carbon.jl      # Optional Julia overlay module
    data/
        emission_factors.csv
    tests/
        test_tracker.py
```

### plugin.json Format

JSON manifest describing the plugin:

```json
{
    "name": "carbon_tracker",
    "version": "1.0.0",
    "description": "Track CO2 emissions per generator and export annual carbon reports",
    "author": "Jane Doe",
    "url": "https://github.com/janedoe/esfex-carbon-tracker",
    "category": "analysis",
    "priority": 0,
    "requires_plugins": [],
    "python_dependencies": ["pandas>=2.0"]
}
```

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | Yes | string | Unique slug identifier. Must match `^[A-Za-z0-9][A-Za-z0-9_-]*$` (alphanumeric, underscore, hyphen). |
| `version` | Yes | string | Semantic version string (e.g., `"1.2.3"`). |
| `description` | No | string | Human-readable description. |
| `author` | No | string | Author name or organization. |
| `url` | No | string | Homepage or repository URL. |
| `category` | No | string | One of: `"data"`, `"analysis"`, `"visualization"`, `"model"`, `"general"`. Default: `"general"`. |
| `priority` | No | int | Execution order for hooks. Lower values execute first. Default: `0`. |
| `requires_plugins` | No | list[str] | Names of plugins that must be loaded before this one. Missing dependencies produce a warning but do not block loading. |
| `python_dependencies` | No | list[str] | Pip requirement strings for informational display in the GUI (not auto-installed). |

### Entry Point: `__init__.py`

Must define a `create_plugin(context)` factory function returning a `ESFEXPlugin` instance:

```python
"""Carbon Tracker plugin for ESFEX."""

from esfex.plugins.protocol import PluginContext, ESFEXPlugin


class CarbonTrackerPlugin(ESFEXPlugin):
    """Tracks CO2 emissions across the simulation."""

    def setup(self) -> None:
        """Called once after instantiation."""
        self._emissions = {}  # year -> total tonnes

    def teardown(self) -> None:
        """Called when the plugin manager shuts down."""
        self._emissions.clear()


def create_plugin(context: PluginContext) -> ESFEXPlugin:
    """Factory function called by the PluginManager."""
    return CarbonTrackerPlugin(context)
```


---


## ESFEXPlugin Base Class

Defined in `esfex.plugins.protocol`. Provides no-op defaults for every hook; override only the methods you need.

```python
class ESFEXPlugin:
    meta: PluginMeta

    def __init__(self, context: PluginContext) -> None:
        self.context = context
```

### Key Properties via `self.context`

| Property | Type | Description |
|----------|------|-------------|
| `context.config` | `ESFEXConfig` or `None` | The loaded ESFEX configuration. May be `None` if the plugin is loaded before config parsing. |
| `context.plugin_dir` | `Path` | Root directory of the plugin (where `plugin.json` lives). Use this to locate data files bundled with your plugin. |
| `context.data_dir` | `Path` | Persistent data directory for this plugin: `~/.esfex/plugin_data/{name}/`. Created automatically. Use this for caches, logs, or generated artifacts. |
| `context.gui_mode` | `bool` | `True` when running inside the Studio. Use this to conditionally import PySide6 modules. |

### Lifecycle Methods

```python
def setup(self) -> None:
    """Called after the plugin is instantiated. Perform one-time init."""

def teardown(self) -> None:
    """Called when the plugin manager shuts down. Release resources."""
```

### Configuration Hook

```python
def get_config_schema(self) -> Optional[type[BaseModel]]:
    """Return a Pydantic model validating the plugins.{name} config section."""
    return None

def on_config_loaded(self, config: ESFEXConfig) -> None:
    """Called after the full configuration has been loaded and validated."""
```

Plugin configuration is provided in the YAML file under `plugins.<name>`:

```yaml
plugins:
  carbon_tracker:
    emission_factor_file: "data/emission_factors.csv"
    report_format: "xlsx"
```


---


## Simulation Hooks

Called by the runner during simulation. Each hook is wrapped in `try/except` --- a broken plugin logs an error but never crashes the simulation.

### pre_simulation

```python
def pre_simulation(self, *, config: ESFEXConfig, output_dir: Path) -> None:
    """Called before the simulation starts.

    Use this to initialize output files, set up logging, or validate
    plugin-specific configuration.
    """
```

### post_demand_loaded

```python
def post_demand_loaded(
    self,
    *,
    base_demand: np.ndarray,
    ev_demand: np.ndarray,
    total_demand: np.ndarray,
    config: ESFEXConfig,
) -> Optional[np.ndarray]:
    """Called after demand is loaded and EV demand is computed.

    Return a modified total_demand array to replace the default, or
    None to keep it unchanged. This is the right place to add
    demand-side modifications like demand response programs or
    climate-adjusted load profiles.
    """
    return None
```

### pre_master_problem / post_master_problem

```python
def pre_master_problem(self, *, config: ESFEXConfig, years: list[int]) -> None:
    """Called before the master problem is solved.

    Use this to log planning parameters or prepare data structures
    for tracking investment decisions.
    """

def post_master_problem(
    self,
    *,
    investments: dict[str, Any],
    retirements: dict[str, Any],
    config: ESFEXConfig,
) -> None:
    """Called after master problem solution.

    The investments dict contains keys like 'gen_investment_power_G_N'
    with MW values. The retirements dict contains retirement flags.
    """
```

### pre_year / post_year

```python
def pre_year(
    self,
    *,
    year: int,
    year_idx: int,
    units_config: dict[str, Any],
    config: ESFEXConfig,
) -> None:
    """Called before each year's operational dispatch.

    units_config contains the active generators and batteries for this
    year, including virtual units from technology investments.
    """

def post_year(
    self,
    *,
    year: int,
    result: Any,
    hdf5_file: Any,
    output_dir: Path,
    config: ESFEXConfig,
) -> None:
    """Called after each year's results are available.

    hdf5_file is an open h5py.File in append mode. Plugins should write
    their data to the 'plugins/{name}/' group to avoid namespace conflicts:

        grp = hdf5_file.require_group(f"plugins/{self.meta.name}")
        grp.create_dataset("co2_annual", data=self._year_emissions)
    """
```

### post_simulation

```python
def post_simulation(
    self,
    *,
    results: list[Any],
    hdf5_path: Path,
    output_dir: Path,
    config: ESFEXConfig,
) -> None:
    """Called after all years are complete and HDF5 is finalized.

    Use this for final report generation, cleanup, or cross-year analysis.
    """
```


---


## GUI Hooks

Only called when `context.gui_mode is True`. Guard PySide6 imports behind this check.

### extend_element_tree

```python
def get_tree_categories(self) -> list[dict[str, str]]:
    """Return category descriptors for the element tree.

    Each dict should have:
        {"key": "co2_sources", "label": "CO2 Sources", "element_type": "co2_source"}
    """
    return []
```

### extend_element_forms

```python
def get_forms(self, model: Any) -> list[tuple[str, Any]]:
    """Return (element_type, QWidget) pairs for the properties panel.

    When the user selects an element of the matching type in the tree,
    the provided QWidget is shown in the properties panel.
    """
    return []
```

### extend_toolbar

```python
def get_toolbar_actions(self, toolbar: Any, main_window: Any) -> list[Any]:
    """Return QAction instances to add to the toolbar.

    Example:
        from PySide6.QtGui import QAction
        action = QAction("Run Carbon Report", main_window)
        action.triggered.connect(self._run_report)
        toolbar.addAction(action)
        return [action]
    """
    return []
```

### extend_menu

```python
def get_menu_items(self, menu_bar: Any, main_window: Any) -> None:
    """Add items to the menu bar.

    Example:
        menu = menu_bar.addMenu("Carbon")
        action = menu.addAction("Generate Report...")
        action.triggered.connect(self._show_report_dialog)
    """
```

### extend_results_panel

```python
def get_result_variables(self) -> list[tuple[str, str, str, str]]:
    """Return (display_name, hdf5_key, aggregation, viz_type) tuples.

    These register new result variables that appear in the GUI results panel.

    Example:
        return [
            ("CO2 Emissions", "plugins/carbon_tracker/co2_hourly", "sum", "line"),
            ("Carbon Cost", "plugins/carbon_tracker/carbon_cost", "sum", "bar"),
        ]
    """
    return []
```

### extend_map

```python
def get_map_layers(self, map_widget: Any) -> None:
    """Add custom layers to the Leaflet map widget.

    Use map_widget's JavaScript bridge to add markers, polygons,
    or tile layers.
    """
```

### register_translations

```python
def get_translations(self) -> dict[str, dict[str, str]]:
    """Return {lang: {key: value}} translation mappings.

    Example:
        return {
            "en": {
                "carbon_tracker.title": "Carbon Tracker",
                "carbon_tracker.report": "Generate Report",
            },
            "es": {
                "carbon_tracker.title": "Rastreador de Carbono",
                "carbon_tracker.report": "Generar Informe",
            },
        }
    """
    return {}
```


---


## CLI Hooks

### register_commands

```python
def get_cli_commands(self) -> list[typer.Typer]:
    """Return Typer sub-apps to register as 'esfex <name> ...'.

    Each returned Typer app is mounted as a subcommand group named
    after the plugin.
    """
    return []
```

Example registration:

```python
# In cli_commands.py
import typer
from pathlib import Path

app = typer.Typer(
    name="carbon_tracker",
    help="Carbon tracking and reporting tools.",
)

@app.command("report")
def report(
    results: Path = typer.Argument(..., help="Path to HDF5 results file"),
    output: Path = typer.Option("carbon_report.xlsx", help="Output report path"),
    format: str = typer.Option("xlsx", help="Report format: xlsx, csv, json"),
) -> None:
    """Generate a carbon emissions report from simulation results."""
    typer.echo(f"Reading results from {results}...")
    # ... implementation ...
    typer.echo(f"Report written to {output}")
```

Then in `__init__.py`:

```python
class CarbonTrackerPlugin(ESFEXPlugin):
    def get_cli_commands(self) -> list:
        from .cli_commands import app
        return [app]
```

Registers the command as `esfex carbon_tracker report results.h5`.


---


## Julia Overlay Hooks

Plugins inject Julia code via runtime overlays --- `.jl` files `include()`-d after `ESFEX.jl` loads, able to define new functions and reference existing ESFEX types and variables. Plugin Julia modules never modify ESFEX source files on disk.

### register_julia_overlays

```python
def get_julia_modules(self) -> list[Path]:
    """Return .jl files to include() after ESFEX.jl.

    These modules can define functions invoked by Julia-side callbacks
    during model construction.
    """
    return [self.context.plugin_dir / "julia" / "carbon.jl"]
```

Example overlay module (`julia/carbon.jl`):

```julia
"""
Carbon constraint overlay for ESFEX.

Adds a system-wide CO2 budget constraint to the master problem.
"""

function add_carbon_budget_constraint!(model, vars, input, budget_tonnes)
    hours = input.hours
    generators = input.generators

    total_co2 = @expression(model, sum(
        vars.gen_output[g, n, t] * generators[g].emission_factor[n]
        for g in 1:length(generators)
        for n in 1:input.num_nodes
        for t in 1:hours
        if generators[g].emission_factor[n] > 0
    ))

    @constraint(model, carbon_budget, total_co2 <= budget_tonnes)
    return nothing
end
```


---


## Complete Example: Carbon Tracker Plugin

Tracks CO2 emissions per generator across the simulation and exports an annual carbon report.

### Directory Structure

```
carbon_tracker/
    plugin.json
    __init__.py
    tracker.py
    cli_commands.py
    tests/
        test_tracker.py
```

### plugin.json

```json
{
    "name": "carbon_tracker",
    "version": "1.0.0",
    "description": "Track CO2 emissions per generator and export annual carbon reports",
    "author": "ESFEX Contributors",
    "category": "analysis",
    "priority": 10,
    "python_dependencies": ["pandas>=2.0"]
}
```

### __init__.py

```python
"""Carbon Tracker plugin for ESFEX."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from esfex.plugins.protocol import PluginContext, PluginMeta, ESFEXPlugin

logger = logging.getLogger(__name__)


class CarbonTrackerPlugin(ESFEXPlugin):
    """Tracks CO2 emissions across the simulation and writes reports."""

    def setup(self) -> None:
        self._emissions: dict[int, dict[str, float]] = {}
        self._output_dir: Optional[Path] = None
        logger.info("Carbon Tracker plugin initialized")

    def teardown(self) -> None:
        self._emissions.clear()

    # ---- Simulation hooks ------------------------------------------------

    def pre_simulation(self, *, config, output_dir: Path) -> None:
        self._output_dir = output_dir
        self._emissions.clear()

    def post_year(
        self,
        *,
        year: int,
        result: Any,
        hdf5_file: Any,
        output_dir: Path,
        config,
    ) -> None:
        from .tracker import compute_year_emissions

        year_emissions = compute_year_emissions(hdf5_file, year, config)
        self._emissions[year] = year_emissions

        # Write to HDF5 under plugins/ namespace
        grp = hdf5_file.require_group(f"plugins/{self.meta.name}")
        year_grp = grp.require_group(f"year_{year}")
        for gen_name, tonnes in year_emissions.items():
            year_grp.attrs[f"co2_{gen_name}"] = tonnes

        total = sum(year_emissions.values())
        year_grp.attrs["co2_total"] = total
        logger.info("Year %d: total CO2 = %.0f tonnes", year, total)

    def post_simulation(self, *, results, hdf5_path, output_dir, config) -> None:
        from .tracker import write_carbon_report

        report_path = output_dir / "carbon_report.csv"
        write_carbon_report(self._emissions, report_path)
        logger.info("Carbon report written to %s", report_path)

    # ---- CLI hooks -------------------------------------------------------

    def get_cli_commands(self) -> list:
        from .cli_commands import app
        return [app]

    # ---- GUI hooks (conditional on gui_mode) -----------------------------

    def get_menu_items(self, menu_bar, main_window) -> None:
        if not self.context.gui_mode:
            return
        from PySide6.QtGui import QAction

        menu = menu_bar.addMenu("Carbon")
        action = QAction("View Emissions Summary...", main_window)
        action.triggered.connect(
            lambda: self._show_emissions_dialog(main_window)
        )
        menu.addAction(action)

    def get_result_variables(self) -> list[tuple[str, str, str, str]]:
        return [
            (
                "CO2 Emissions (total)",
                "plugins/carbon_tracker/co2_hourly",
                "sum",
                "line",
            ),
        ]

    def _show_emissions_dialog(self, parent) -> None:
        from PySide6.QtWidgets import QMessageBox

        if not self._emissions:
            QMessageBox.information(
                parent, "Carbon Tracker", "No emission data yet. Run a simulation first."
            )
            return

        lines = []
        for year in sorted(self._emissions):
            total = sum(self._emissions[year].values())
            lines.append(f"Year {year}: {total:,.0f} tonnes CO2")

        QMessageBox.information(
            parent, "Carbon Tracker - Emissions Summary", "\n".join(lines)
        )


def create_plugin(context: PluginContext) -> ESFEXPlugin:
    """Factory function called by the PluginManager."""
    return CarbonTrackerPlugin(context)
```

### tracker.py

```python
"""Core emission tracking logic."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default emission factors (tonnes CO2 / MWh) by fuel type
DEFAULT_EMISSION_FACTORS = {
    "diesel": 0.267,
    "fuel_oil": 0.279,
    "natural_gas": 0.181,
    "coal": 0.340,
    "biomass": 0.0,       # Carbon-neutral by convention
    "solar": 0.0,
    "wind": 0.0,
    "hydro": 0.0,
    "nuclear": 0.0,
}


def compute_year_emissions(
    hdf5_file: Any,
    year: int,
    config: Any,
) -> dict[str, float]:
    """Compute CO2 emissions per generator for a given year.

    Parameters
    ----------
    hdf5_file:
        Open h5py.File with simulation results.
    year:
        The simulation year.
    config:
        The ESFEXConfig object.

    Returns
    -------
    dict mapping generator name to CO2 tonnes.
    """
    import numpy as np

    emissions: dict[str, float] = {}

    # Find the year group
    year_key = f"detailed_results/year_{year}_threshold_0"
    if year_key not in hdf5_file:
        logger.warning("Year group %s not found in HDF5", year_key)
        return emissions

    gen_grp = hdf5_file[f"{year_key}/generation"]

    # Build fuel map from config
    fuel_map: dict[str, str] = {}
    for sys_config in _iter_system_configs(config):
        for gen in sys_config.generators:
            fuel_map[gen.name] = gen.fuel.lower()

    for gen_name in gen_grp:
        gen_output = gen_grp[gen_name][:]  # (num_nodes, hours)
        total_mwh = float(np.sum(gen_output))

        fuel = fuel_map.get(gen_name, "unknown")
        factor = DEFAULT_EMISSION_FACTORS.get(fuel, 0.0)
        emissions[gen_name] = total_mwh * factor

    return emissions


def write_carbon_report(
    emissions: dict[int, dict[str, float]],
    output_path: Path,
) -> None:
    """Write a CSV carbon report.

    Parameters
    ----------
    emissions:
        Mapping of year -> {generator_name: tonnes_co2}.
    output_path:
        Path for the output CSV file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Collect all generator names
    all_gens = sorted(
        {name for year_data in emissions.values() for name in year_data}
    )

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Year"] + all_gens + ["Total"])

        for year in sorted(emissions):
            row = [year]
            total = 0.0
            for gen in all_gens:
                val = emissions[year].get(gen, 0.0)
                row.append(f"{val:.1f}")
                total += val
            row.append(f"{total:.1f}")
            writer.writerow(row)

    logger.info("Carbon report: %d years, %d generators -> %s",
                len(emissions), len(all_gens), output_path)


def _iter_system_configs(config):
    """Yield system configs from a ESFEXConfig."""
    if hasattr(config, "systems"):
        yield from config.systems.values()
    elif hasattr(config, "sys"):
        yield config.sys
```

### cli_commands.py

```python
"""CLI subcommands for the carbon_tracker plugin."""

import typer
from pathlib import Path

app = typer.Typer(
    name="carbon_tracker",
    help="Carbon emission tracking and reporting.",
)


@app.command("report")
def report(
    results: Path = typer.Argument(
        ..., exists=True, help="Path to HDF5 results file."
    ),
    output: Path = typer.Option(
        "carbon_report.csv", "--output", "-o", help="Output report path."
    ),
) -> None:
    """Generate a carbon emissions report from simulation results."""
    import h5py
    from .tracker import write_carbon_report

    typer.echo(f"Reading results from {results}...")
    emissions = {}

    with h5py.File(results, "r") as f:
        num_years = f.attrs.get("num_years", 0)
        years_range = f.attrs.get("years_range", "")
        # Scan for year groups
        if "detailed_results" in f:
            for key in f["detailed_results"]:
                if key.startswith("year_"):
                    parts = key.split("_")
                    year = int(parts[1])
                    gen_grp = f[f"detailed_results/{key}/generation"]
                    year_em = {}
                    for gen_name in gen_grp:
                        total = float(gen_grp[gen_name][:].sum())
                        year_em[gen_name] = total * 0.267  # Simplified
                    emissions[year] = year_em

    if not emissions:
        typer.echo("No generation data found in results file.")
        raise typer.Exit(1)

    write_carbon_report(emissions, output)
    typer.echo(f"Report written to {output}")
```


---


## Installation and Distribution

### Local Installation

Copy into any scan location:

```bash
# User-global installation
cp -r carbon_tracker/ ~/.esfex/plugins/carbon_tracker/

# Project-local installation
cp -r carbon_tracker/ /path/to/project/.esfex/plugins/carbon_tracker/
```

### Install from ZIP

Package as a ZIP with the plugin directory as root:

```bash
cd /path/to/plugins/
zip -r carbon_tracker.zip carbon_tracker/
```

Install via CLI or GUI:

```bash
# CLI
esfex plugin install --zip carbon_tracker.zip

# Or via GUI: Plugins > Manage Plugins... > Install from ZIP
```

All paths are validated against Zip Slip (CWE-22) before extraction.

### Install from Git

Install directly from a git repository:

```bash
esfex plugin install --git https://github.com/user/esfex-carbon-tracker.git
```

Only `https://` and `git://` URL schemes are accepted. Git hooks are disabled during clone to prevent pre-checkout remote code execution.

### pip-Installable Plugins

For plugins with their own dependencies, create a standard Python package:

```python
# setup.py or pyproject.toml
# After pip install, the plugin directory is placed in ~/.esfex/plugins/
```

The directory-based method is simplest --- no pip or PyPI integration required.

### Plugin Management CLI

```bash
# List all discovered plugins
esfex plugin list

# Install from ZIP
esfex plugin install --zip /path/to/plugin.zip

# Install from git
esfex plugin install --git https://github.com/user/esfex-plugin.git

# Uninstall
esfex plugin uninstall carbon_tracker

# Enable / disable (persisted across sessions in ~/.esfex/plugins.json)
esfex plugin enable carbon_tracker
esfex plugin disable carbon_tracker
```

### GUI Plugin Manager

Accessible via **Plugins > Manage Plugins...** in the Studio. Provides:

- A table of all discovered plugins with enable/disable checkboxes
- Install from ZIP button (opens file dialog)
- Install from Git button (prompts for URL)
- Uninstall button (with confirmation)
- Open Plugins Folder button (opens `~/.esfex/plugins/` in file manager)

Newly installed plugins are hot-loaded (GUI extensions appear immediately). Disabling a loaded plugin requires a restart.


---


## Testing Plugins

### Unit Testing with Mock Context

Mock `PluginContext` for testing without a full ESFEX installation:

```python
"""tests/test_tracker.py"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from esfex.plugins.protocol import PluginContext


@pytest.fixture
def plugin_context(tmp_path):
    """Create a mock plugin context."""
    plugin_dir = tmp_path / "carbon_tracker"
    plugin_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    return PluginContext(
        config=None,
        plugin_dir=plugin_dir,
        data_dir=data_dir,
        gui_mode=False,
    )


@pytest.fixture
def plugin(plugin_context):
    """Create an instance of the plugin."""
    # Import here to avoid import errors if esfex is not installed
    from carbon_tracker import create_plugin

    p = create_plugin(plugin_context)
    p.setup()
    return p


def test_setup(plugin):
    """Plugin should initialize with empty emissions."""
    assert plugin._emissions == {}


def test_pre_simulation(plugin, tmp_path):
    """pre_simulation should set output directory."""
    config = MagicMock()
    plugin.pre_simulation(config=config, output_dir=tmp_path)
    assert plugin._output_dir == tmp_path
    assert plugin._emissions == {}


def test_compute_year_emissions():
    """Test emission computation with mock HDF5 data."""
    import numpy as np
    from carbon_tracker.tracker import compute_year_emissions

    # Create a mock HDF5-like structure
    mock_file = MagicMock()
    mock_file.__contains__ = lambda self, key: key == "detailed_results/year_2030_threshold_0"

    gen_grp = MagicMock()
    gen_grp.__iter__ = lambda self: iter(["diesel_gen"])
    gen_grp.__getitem__ = lambda self, key: MagicMock(
        __getitem__=lambda s, k: np.ones((3, 8760)) * 50  # 50 MW * 8760h
    )
    mock_file.__getitem__ = lambda self, key: gen_grp

    config = MagicMock()
    config.sys.generators = [
        MagicMock(name="diesel_gen", fuel="diesel")
    ]

    # This is a simplified test; real tests would use actual h5py
    # or a more complete mock structure
```

### Integration Testing

Full plugin loading mechanism for integration tests:

```python
def test_plugin_discovery(tmp_path):
    """Test that the plugin manager discovers the plugin."""
    from esfex.plugins.manager import PluginManager

    # Create a minimal plugin structure
    plugin_dir = tmp_path / "test_plugin"
    plugin_dir.mkdir()

    (plugin_dir / "plugin.json").write_text(
        '{"name": "test_plugin", "version": "0.1.0"}'
    )
    (plugin_dir / "__init__.py").write_text(
        "from esfex.plugins.protocol import PluginContext, ESFEXPlugin\n"
        "class TestPlugin(ESFEXPlugin): pass\n"
        "def create_plugin(ctx): return TestPlugin(ctx)\n"
    )

    import os
    os.environ["ESFEX_PLUGIN_PATH"] = str(tmp_path)

    try:
        pm = PluginManager()
        names = pm.discover()
        assert "test_plugin" in names
    finally:
        del os.environ["ESFEX_PLUGIN_PATH"]


def test_plugin_hook_dispatch(tmp_path):
    """Test that hooks are dispatched correctly."""
    from esfex.plugins.manager import PluginManager

    pm = PluginManager()
    # ... setup plugin ...
    pm.load_all()

    # call_hook collects return values from all plugins
    results = pm.call_hook("pre_simulation", config=mock_config, output_dir=tmp_path)
    # Results is a list of non-None return values
```

### Testing Julia Overlays

Test overlay modules in standalone Julia:

```julia
# test/test_carbon.jl
include("../julia/carbon.jl")

using JuMP, HiGHS

model = Model(HiGHS.Optimizer)
# ... set up a minimal model ...
# ... call add_carbon_budget_constraint!(model, vars, input, 1000.0) ...
optimize!(model)
@assert termination_status(model) == MOI.OPTIMAL
```


---


## Security Considerations

### Plugin Sandboxing

Plugins run in the same Python process as the core application with no process-level sandboxing:

- Plugins have full access to the filesystem, network, and Python runtime
- A malicious plugin could read sensitive files, exfiltrate data, or modify ESFEX behavior
- Only install plugins from trusted sources

### Safety Mechanisms

Built-in safety measures:

1. **Hook isolation**: Every hook call is wrapped in `try/except`. A crashing plugin logs an error but does not affect the core application or other plugins.

2. **Name sanitization**: Plugin names must match `^[A-Za-z0-9][A-Za-z0-9_-]*$`. Names with path separators, dots, or special characters are rejected, preventing directory traversal attacks.

3. **Zip Slip protection**: Before extracting any ZIP archive, all member paths are validated against the target directory. Archives containing entries that would escape the target (e.g., `../../etc/passwd`) are rejected with a `ValueError`.

4. **Git clone hardening**: Git hooks are disabled during `git clone` by setting `core.hooksPath` to an empty temporary directory. This prevents pre-checkout hooks from executing arbitrary code. Only `https://` and `git://` URL schemes are accepted.

5. **Audit logging**: The SHA-256 hash of every loaded plugin directory is logged, enabling forensic analysis of which code was executed.

6. **Overwrite protection**: Installing a plugin over an existing one requires explicit `force=True` or user confirmation in the GUI dialog.

### Trusted vs. Untrusted Plugins

| Source | Trust Level | Recommendation |
|--------|-------------|----------------|
| Bundled with ESFEX | Trusted | Safe to use |
| Official repository | Trusted | Review changelogs before updating |
| Known author/org | Semi-trusted | Review source code before first install |
| Unknown source | Untrusted | Review ALL source code; test in a sandbox environment first |

### Best Practices for Plugin Authors

- Do not require or access credentials beyond what your plugin strictly needs
- Document all network access (API calls, data downloads) in your README
- Pin dependency versions to avoid supply chain attacks
- Include a LICENSE file in your plugin directory
- Provide checksums or signatures for release archives


---


## API Reference Summary

### Key Imports

```python
from esfex.plugins.protocol import ESFEXPlugin, PluginContext, PluginMeta
from esfex.plugins.manager import get_plugin_manager, reset_plugin_manager
```

### PluginMeta Fields

| Field | Type | Default |
|-------|------|---------|
| `name` | `str` | (required) |
| `version` | `str` | (required) |
| `description` | `str` | `""` |
| `author` | `str` | `""` |
| `url` | `str` | `""` |
| `requires_plugins` | `list[str]` | `[]` |
| `priority` | `int` | `0` |
| `category` | `str` | `"general"` |
| `python_dependencies` | `list[str]` | `[]` |

### ESFEXPlugin Method Reference

| Method | Category | Returns | Description |
|--------|----------|---------|-------------|
| `setup()` | Lifecycle | `None` | One-time initialization after instantiation |
| `teardown()` | Lifecycle | `None` | Cleanup when shutting down |
| `get_config_schema()` | Config | `type[BaseModel]` or `None` | Pydantic model for plugin config validation |
| `on_config_loaded(config)` | Config | `None` | Called after config is loaded |
| `pre_simulation(...)` | Runner | `None` | Before simulation starts |
| `post_demand_loaded(...)` | Runner | `np.ndarray` or `None` | After demand loading; return modified demand |
| `pre_master_problem(...)` | Runner | `None` | Before master problem solve |
| `post_master_problem(...)` | Runner | `None` | After master problem solve |
| `pre_year(...)` | Runner | `None` | Before each year's dispatch |
| `post_year(...)` | Runner | `None` | After each year's results |
| `post_simulation(...)` | Runner | `None` | After all years complete |
| `get_julia_modules()` | Julia | `list[Path]` | Julia overlay files to include |
| `get_cli_commands()` | CLI | `list[Typer]` | Typer sub-apps for CLI |
| `get_tree_categories()` | GUI | `list[dict]` | Element tree categories |
| `get_forms(model)` | GUI | `list[tuple]` | Property panel forms |
| `get_toolbar_actions(toolbar, window)` | GUI | `list[QAction]` | Toolbar actions |
| `get_menu_items(menu_bar, window)` | GUI | `None` | Menu bar items |
| `get_result_variables()` | GUI | `list[tuple]` | Result panel variables |
| `get_map_layers(map_widget)` | GUI | `None` | Map overlays |
| `get_translations()` | GUI | `dict` | i18n translations |

### PluginManager Key Methods

| Method | Description |
|--------|-------------|
| `discover(project_dir=None)` | Scan directories and return discovered plugin names |
| `load_all(config, gui_mode, project_dir)` | Discover, load, and setup all enabled plugins |
| `load_single(name, config, gui_mode)` | Hot-load a single plugin by name |
| `call_hook(hook_name, **kwargs)` | Dispatch a hook to all loaded plugins |
| `enable(name)` / `disable(name)` | Persist enable/disable state |
| `install_from_zip(path, force)` | Install plugin from ZIP archive |
| `install_from_git(url, target_name, force)` | Install plugin from git repository |
| `uninstall(name)` | Remove plugin from user plugins directory |
| `register_julia_modules()` | Collect and register Julia overlay modules |
| `register_cli_commands(app)` | Register plugin CLI subcommands on Typer app |
| `register_gui_extensions(window)` | Register all GUI extensions on main window |
| `teardown_all()` | Teardown all plugins in reverse order |
