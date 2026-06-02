# CLI Reference

All commands are accessed through the `esfex` entry point, built with [Typer](https://typer.tiangolo.com/) and formatted with [Rich](https://rich.readthedocs.io/).

## Commands Overview

| Command | Description |
|---------|-------------|
| `esfex run` | Run the optimization model |
| `esfex validate` | Validate a configuration file |
| `esfex export` | Export results to different formats |
| `esfex studio` | Launch the GIS-based Studio |
| `esfex plugin` | Manage plugins (install, enable, disable, list) |
| `esfex info` | Show version and system information |

---


## `esfex run`

```bash
esfex run -c CONFIG [OPTIONS]
```

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--config` | `-c` | Path | *required* | Path to YAML configuration file |
| `--mode` | `-m` | String | `development` | Simulation mode: `development` or `unit_commitment` |
| `--solver` | `-s` | String | `highs` | Solver: `highs`, `cbc`, `glpk`, `gurobi`, `cplex`, `scip`, `xpress` |
| `--output` | `-o` | Path | `./results` | Output directory for results |
| `--years` | `-y` | Integer | from config | Number of years to simulate |
| `--verbose` | `-v` | Flag | `false` | Enable verbose output (DEBUG-level logging) |
| `--dry-run` | | Flag | `false` | Validate config and show plan without running |

### Examples

```bash
# Basic run with default settings (HiGHS solver, development mode)
esfex run -c my_system.yaml

# 10-year simulation with verbose output
esfex run -c my_system.yaml -y 10 -v

# Use Gurobi solver with custom output directory
esfex run -c my_system.yaml -s gurobi -o ./gurobi_results

# Dry run: validate and preview configuration without optimization
esfex run -c my_system.yaml --dry-run

# Unit commitment mode (MIP with binary commitment variables)
esfex run -c my_system.yaml -m unit_commitment
```

### Output

- **Console**: Real-time progress with year-by-year summaries, investment decisions, and solve times
- **HDF5**: `results/{system_name}.h5` with all optimization results
- **MGA**: `results/mga_{system_name}.h5` when MGA/SPORES is enabled
- **Log**: Detailed logging to stderr when `--verbose` is enabled

#### Typical Console Output

```
ESFEX - Power System Optimization
Configuration: island_system.yaml
Mode: development
Solver: highs

┌──────────────────────┬────────────────┐
│ Setting              │ Value          │
├──────────────────────┼────────────────┤
│ Simulation Mode      │ development    │
│ Solver               │ highs          │
│ Systems              │ island         │
│   island nodes       │ 3              │
│   island generators  │ 5              │
│   island batteries   │ 2              │
│ Rolling Horizon      │ True           │
│ Primary Energy       │ False          │
│ N-1 Security         │ False          │
└──────────────────────┴────────────────┘

Year 1/25: Solving master problem... Done (12.3s)
  Investments: Solar PV +87.2 MW (node 0), Li-Ion +24.0 MW / 96.0 MWh (node 0)
Year 1/25: Solving 182 operational windows... Done (45.1s)
  Objective: $8,234,567  RE: 62.3%  Load shed: 0.0 MWh
...
Optimization completed successfully!
Results saved to: ./results
```

#### Dry-Run Output

Validates the configuration and displays the summary table without launching Julia:

```
ESFEX - Power System Optimization
Configuration: island_system.yaml
Mode: development
Solver: highs

┌──────────────────────┬────────────────┐
│ Setting              │ Value          │
│ ...                  │ ...            │
└──────────────────────┴────────────────┘

Dry run mode - not executing optimization
```

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success (or dry-run completed) |
| `1` | Configuration error or optimization failure |


---


## `esfex validate`

Performs Pydantic schema validation, type checking, per-node array length verification, and file existence checks without running the optimization.

```bash
esfex validate -c CONFIG
```

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--config` | `-c` | Path | *required* | Path to YAML configuration file |

### Example: Successful Validation

```bash
esfex validate -c island_system.yaml
```

Output on success:

```
Validating: island_system.yaml
Configuration is valid!
┌──────────────────────┬────────────────┐
│ Setting              │ Value          │
├──────────────────────┼────────────────┤
│ Simulation Mode      │ development    │
│ Solver               │ highs          │
│ Systems              │ island         │
│   island nodes       │ 3              │
│   island generators  │ 5              │
│   island batteries   │ 2              │
│ Rolling Horizon      │ True           │
│ Primary Energy       │ False          │
│ N-1 Security         │ False          │
└──────────────────────┴────────────────┘
```

### Example: Failed Validation

```bash
esfex validate -c broken_config.yaml
```

Output on failure:

```
Validating: broken_config.yaml
Validation failed:
  generators.solar_pv.rated_power: list length must match num_nodes (3)
```

Exits with code `1` on failure, suitable for CI/CD pipelines.


---


## `esfex export`

Converts HDF5 results to CSV, Excel, or JSON for post-processing in external tools (Excel, R, pandas, etc.).

```bash
esfex export -r RESULTS [OPTIONS]
```

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--results` | `-r` | Path | *required* | Path to HDF5 results file |
| `--format` | `-f` | String | `csv` | Export format: `csv`, `excel`, `json` |
| `--output` | `-o` | Path | `{results_dir}/export/` | Output directory or file path |

### Examples

```bash
# Export to CSV (creates one file per dataset, organized in subdirectories)
esfex export -r results/island.h5 -f csv

# Export to CSV in a custom directory
esfex export -r results/island.h5 -f csv -o results/csv_export/

# Export to Excel workbook (single .xlsx file with multiple sheets)
esfex export -r results/island.h5 -f excel -o results/report.xlsx

# Export to JSON (nested structure with metadata)
esfex export -r results/island.h5 -f json -o results/data.json
```

### CSV Export Structure

Creates a directory tree mirroring the HDF5 hierarchy:

```
results/export/
├── summary/
│   ├── investments.csv
│   ├── retirements.csv
│   ├── objectives.csv
│   └── re_penetration.csv
├── island/
│   ├── generation/
│   │   ├── Solar_PV.csv
│   │   ├── Diesel.csv
│   │   └── ...
│   ├── curtailment.csv
│   ├── electricity_prices.csv
│   ├── battery_charge/
│   │   └── Li_Ion.csv
│   └── ...
└── demand/
    ├── base_demand.csv
    └── total_demand.csv
```

### Excel Export

Single workbook with a `Summary` sheet (investments, retirements) plus selected operational data sheets.

### JSON Export

Structured file with metadata and summary results. Full hourly time-series data is omitted; use CSV or direct HDF5 access for time-series analysis.


---


## `esfex studio`

Interactive GIS-based grid editor (Leaflet.js + OpenStreetMap) for placing nodes, generators, batteries, and transmission lines.

```bash
esfex studio [OPTIONS]
```

### Prerequisites

Requires PySide6 and QWebEngine:

```bash
pip install "esfex[gui]"
```

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--config` | `-c` | Path | None | Existing YAML config to load and edit |
| `--output` | `-o` | Path | auto | Output YAML path for saving |

### Examples

```bash
# Create a new system from scratch (opens empty map)
esfex studio

# Edit an existing configuration (loads all systems, nodes, equipment)
esfex studio -c island_system.yaml

# Edit and save to a different file
esfex studio -c island_system.yaml -o island_system_v2.yaml
```

### Editor Features

- **Multi-system support**: Create and switch between multiple power systems
- **Drag-and-drop placement**: Nodes, generators, batteries, transformers, fuel entry points
- **Polyline transmission lines**: Draw lines with waypoints that snap to equipment
- **Property forms**: Edit all parameters (costs, capacities, efficiencies) in side panels
- **Resource assessment wizards**: Solar PV, Wind, OTEC profile generation
- **Demand distribution**: Visual demand allocation across nodes
- **Validation**: Real-time validation of system connectivity and parameter consistency
- **Export**: Save complete YAML configuration file

### Console Output

```
ESFEX - Studio
Loading: island_system.yaml
```

When the editor window closes:

```
Configuration saved to: island_system.yaml
```

Or if closed without saving:

```
Editor closed without saving.
```

---


## `esfex plugin`

Plugins are directories stored in one of three locations:

1. **User-level**: `~/.esfex/plugins/`
2. **Project-level**: `<project>/.esfex/plugins/`
3. **Environment variable**: Paths listed in `ESFEX_PLUGIN_PATH` (colon-separated on Linux/macOS, semicolon on Windows)

No pip or PyPI integration required. Plugins are standalone directories containing a manifest and Python code.

### Sub-commands

| Sub-command | Description |
|-------------|-------------|
| `esfex plugin list` | List all discovered plugins and their status |
| `esfex plugin install` | Install a plugin from git or ZIP |
| `esfex plugin uninstall NAME` | Remove a plugin directory |
| `esfex plugin enable NAME` | Enable a disabled plugin |
| `esfex plugin disable NAME` | Disable a plugin without uninstalling |

### `esfex plugin list`

```bash
esfex plugin list
```

Example output:

```
                    ESFEX Plugins
┌──────────────────────┬─────────┬──────────┬─────────┬──────────────────────────────┐
│ Name                 │ Version │ Category │ Status  │ Description                  │
├──────────────────────┼─────────┼──────────┼─────────┼──────────────────────────────┤
│ availability_gen     │ 0.1.0   │ data     │ enabled │ Generate availability CSVs   │
│ weather_forecast     │ 0.2.1   │ data     │ disabled│ Weather-based demand forecast│
└──────────────────────┴─────────┴──────────┴─────────┴──────────────────────────────┘
```

If no plugins are found:

```
No plugins found.
Plugins are discovered from:
  ~/.esfex/plugins/
  <project>/.esfex/plugins/
  $ESFEX_PLUGIN_PATH
```

### `esfex plugin install`

| Option | Type | Description |
|--------|------|-------------|
| `--git` | String | Git URL to clone |
| `--zip` | Path | ZIP file to extract |
| `--name` | String | Target directory name (for git installs) |

```bash
# Install from GitHub
esfex plugin install --git https://github.com/user/esfex-weather

# Install from GitHub with a custom directory name
esfex plugin install --git https://github.com/user/esfex-weather --name weather_plugin

# Install from ZIP file
esfex plugin install --zip weather_plugin.zip
```

Output on success:

```
Installed plugin: weather_forecast
```

### `esfex plugin uninstall`

```bash
esfex plugin uninstall weather_forecast
```

Output:

```
Uninstalled plugin: weather_forecast
```

### `esfex plugin enable` / `disable`

Disabled plugins are not loaded during optimization or GUI startup.

```bash
# Disable a plugin (persists across sessions)
esfex plugin disable weather_forecast

# Re-enable it
esfex plugin enable weather_forecast
```

Output:

```
Disabled plugin: weather_forecast
Enabled plugin: weather_forecast
```

### Plugin CLI Extensions

Plugins can register CLI sub-commands under `esfex`. Registered commands appear automatically when the plugin is enabled.


---


## `esfex info`

Displays version information, Python/Julia availability, and installed solver status.

```bash
esfex info
```

### Example Output

```
ESFEX version 0.1.0
Python: 3.12.0 (main, Oct  2 2023, 12:00:00) [GCC 13.2.0]
Julia: Available via juliacall

Available solvers (Julia/JuMP):
  HiGHS: Available (v1.7.0)
  Gurobi: Not found
  CPLEX: Not found
```

If Julia is not installed or `juliacall` is not available:

```
ESFEX version 0.1.0
Python: 3.12.0
Julia: Not available (juliacall not installed)

Available solvers (Julia/JuMP):
  Julia not available - cannot check solvers
```

### Solver Detection

Two detection mechanisms:

1. **Python-side**: Fast checks for companion packages (`gurobipy`, `cplex`, `pyscipopt`, `xpress`)
2. **Julia-side**: Imports the Julia package (`HiGHS.jl`, `Gurobi.jl`, `CPLEX.jl`) and reports its version

HiGHS and GLPK are always available as bundled Julia project dependencies.


---


## Environment Variables

| Variable | Description |
|----------|-------------|
| `ESFEX_PLUGIN_PATH` | Additional directories to search for plugins (colon-separated) |
| `CPLEX_STUDIO_BINARIES` | Path to CPLEX binaries (required for CPLEX solver) |
| `GUROBI_HOME` | Path to Gurobi installation (required for Gurobi solver) |

---


## Shell Completion

```bash
# Bash
esfex --install-completion bash

# Zsh
esfex --install-completion zsh

# Fish
esfex --install-completion fish
```

After installation, restart your shell:

```bash
esfex ru<TAB>     # completes to "esfex run"
esfex run -<TAB>  # shows available options
```
