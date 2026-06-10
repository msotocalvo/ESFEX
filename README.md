<p align="center">
  <img src="docs/assets/esfex.png" alt="ESFEX Logo" width="460"/>
</p>

<h1 align="center">ESFEX — Energy System Flexibility Studio</h1>

<p align="center">
  <strong>A framework for power system capacity expansion and operational dispatch under high renewable penetration</strong>
</p>

<p align="center">
  <a href="https://github.com/Net-Zero-Horizon/ESFEX/actions/workflows/ci.yml">
    <img src="https://github.com/Net-Zero-Horizon/ESFEX/actions/workflows/ci.yml/badge.svg" alt="CI">
  </a>
  <a href="https://codecov.io/gh/Net-Zero-Horizon/ESFEX">
    <img src="https://codecov.io/gh/Net-Zero-Horizon/ESFEX/branch/main/graph/badge.svg?flag=python" alt="codecov (python)">
  </a>
  <a href="https://esfex.readthedocs.io/">
    <img src="https://readthedocs.org/projects/esfex/badge/?version=latest" alt="Documentation">
  </a>
  <a href="https://doi.org/10.5281/zenodo.20504838">
    <img src="https://zenodo.org/badge/DOI/10.5281/zenodo.20504838.svg" alt="DOI">
  </a>
  <a href="https://pypi.org/project/esfex/">
    <img src="https://img.shields.io/pypi/pyversions/esfex.svg" alt="Python versions">
  </a>
  <a href="https://julialang.org/">
    <img src="https://img.shields.io/badge/Julia-1.9%2B-9558B2.svg" alt="Julia">
  </a>
  <a href="https://jump.dev/">
    <img src="https://img.shields.io/badge/optimization-JuMP-2C8C3C.svg" alt="JuMP">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License">
  </a>
  <a href="https://api.reuse.software/info/github.com/Net-Zero-Horizon/ESFEX">
    <img src="https://api.reuse.software/badge/github.com/Net-Zero-Horizon/ESFEX" alt="REUSE status">
  </a>
  <a href="https://www.bestpractices.dev/projects/13101">
    <img src="https://www.bestpractices.dev/projects/13101/badge" alt="OpenSSF Best Practices">
  </a>
  <a href="https://github.com/astral-sh/ruff">
    <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Ruff">
  </a>
  <a href="https://pypi.org/project/esfex/">
    <img src="https://img.shields.io/pypi/v/esfex.svg" alt="PyPI">
  </a>
  <a href="https://pepy.tech/project/esfex">
    <img src="https://static.pepy.tech/badge/esfex" alt="Downloads">
  </a>
  <a href="https://github.com/Net-Zero-Horizon/ESFEX/commits/main">
    <img src="https://img.shields.io/github/last-commit/Net-Zero-Horizon/ESFEX.svg" alt="Last commit">
  </a>
  <img src="https://img.shields.io/badge/status-alpha-orange.svg" alt="Status">
</p>

<p align="center">
  <a href="#overview">Overview</a> •
  <a href="#key-features">Features</a> •
  <a href="#installation">Installation</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#the-studio">Studio</a> •
  <a href="#documentation">Documentation</a> •
  <a href="#citation">Citation</a>
</p>

---

## Overview

**ESFEX** (Energy System Flexibility) is an open-source power system planning framework that co-optimizes generation, storage, and transmission investment over multi-decade horizons while explicitly capturing the operational flexibility constraints that arise in systems with high shares of variable renewable energy.

It couples a strategic **capacity expansion planner** (Master Problem) with a detailed **operational dispatch engine** through a two-stage decomposition — bridging the gap between long-term investment planning tools and short-term production cost models. Investment decisions are validated operationally (ramp rates, minimum stable generation, storage cycling, demand response, sector coupling) *before* being accepted, so the plan that ESFEX produces is one the system can actually operate.

ESFEX is implemented as a hybrid system: **Python** handles configuration, data management, orchestration, the GIS Studio, and post-processing; **Julia** (via [JuMP](https://jump.dev/)) handles the mathematical optimization, leveraging its compiled performance for large-scale LP and MIP problems. The two communicate through [`juliacall`](https://github.com/JuliaPy/PythonCall.jl). The architecture is modular: seven interlinked optimization models can be selectively enabled depending on the study scope.

### Target Applications

- **Island power systems and isolated grids** transitioning from diesel dependence to high RE penetration
- **Regional transmission planning** with DC and AC power flows, N-1 security, and transmission investment
- **Sector coupling studies** combining electricity, hydrogen (electrolyzer), fuel logistics (primary energy), and electric vehicles (V2G)
- **Policy analysis** evaluating RE targets, CO₂ budgets, storage mandates, and technology cost trajectories
- **Near-optimal space exploration** via MGA (Hop-Skip-Jump) or SPORES (per-objective sweep) for robust investment strategies under uncertainty
- **Academic research** in energy systems optimization, flexibility quantification, and capacity expansion methodology

---

## Key Features

### Optimization Architecture

- **Two-stage decomposition** — Master Problem (all years simultaneously, representative days/periods) + Operational Dispatch (year-by-year, full chronological year). Investments are operationally validated before acceptance.
- **Rolling horizon dispatch** — Configurable overlapping time windows with boundary-condition propagation (battery SOC, generator status) and automatic result stitching.
- **Three simulation modes** — `development` (LP, continuous commitment + investment), `economic_dispatch` (LP, fixed fleet), `unit_commitment` (MIP, binary startup/shutdown with min up/down times).
- **Unit decommissioning planning** — Age-based retirement plus NPV-based retirement for flexible phase-out / retention of the unit inventory.

### Power System Modeling

- **DC power flow** — KCL/KVL constraints with a cycle-based formulation for meshed networks, voltage angle variables, piecewise-linear losses, and transmission investment.
- **AC optimal power flow** — Four selectable ACOPF formulations: SOC relaxation (convex W-space), QC relaxation (McCormick envelopes), Polar NLP (exact V-θ), and Rectangular NLP (exact e-f), solved with Ipopt. Models voltage magnitudes, reactive balance, apparent-power limits (`P² + Q² ≤ S²`).
- **AC power flow verification** — Post-DC Newton-Raphson AC power flow (native Julia solver + pandapower bridge for IEC 60909 short-circuit analysis) to validate voltage profiles and detect violations the DC approximation misses.
- **N-1 security** — Automatic critical-contingency identification with post-contingency flow redistribution for generation and transmission, in both DC and AC.
- **Frequency stability** — Post-contingency ROCOF, frequency nadir, and steady-state frequency via a center-of-inertia (COI) model, with N-1 screening of online generators.
- **Battery storage** — Cyclic SOC, charge/discharge efficiency, calendar + throughput degradation, power/energy co-optimization with duration bounds.
- **Flexible demand** — Multi-sector decomposition with criticality-weighted load shedding and intra-day shifting of deferrable loads.

### Sector Coupling

ESFEX treats sector coupling as a first-class architectural principle. Any energy end-use — electrical, thermal, chemical, or kinetic — can be represented as a demand with its own temporal profile, criticality, and coupling constraints, so arbitrary power-to-X / X-to-power pathways can be modeled without touching the core formulation.

- **Electrolyzer (P2H₂)** — Power-to-hydrogen with capacity investment, load-dependent efficiency, ramp constraints, and coupling to both the electrical balance and hydrogen demand.
- **Primary energy supply chain** — Multi-fuel import nodes, storage tanks, and transport links (pipelines/tankers) coupled to generator fuel consumption.
- **Electric vehicles** — Multi-method fleet adoption, multi-category vehicles (passenger, bus, truck…), time-of-day charging, and bidirectional V2G optimization, via [evrex](https://github.com/Net-Zero-Horizon/evrex).
- **Rooftop solar** — Stochastic adoption with behind-the-meter generation modeled as negative demand, via [rooftex](https://github.com/Net-Zero-Horizon/rooftex).
- **Flexible sectoral demand** — Sector-specific criticality and temporal flexibility for demand-side participation in system balancing.

### Planning and Analysis

- **MGA and SPORES** — Near-optimal alternatives under a shared cost-slack envelope: classical Hop-Skip-Jump diversity (MGA) and per-objective sweeps (SPORES: minimum build, technology equity, regional equity, evolutionary distance).
- **Stochastic programming** — Scenario-based expansion with probability-weighted costs and shared investment variables (EVPI/VSS analysis).
- **Sobol sensitivity analysis** — Global sensitivity indices quantifying how input uncertainty (costs, demand growth, availability) propagates to investment decisions and system cost.
- **Progressive RE targets** — Linear interpolation from initial to target RE penetration with annual increment bounds and constraint-based curtailment limits.

### Tools and Interface

- **GIS-based Studio** — A PySide6 + Leaflet.js map for visually building power systems: place nodes, generators, batteries, and transmission lines with polyline routing. Includes resource-assessment wizards for rooftop solar, utility-scale PV ([solarex](https://github.com/Net-Zero-Horizon/solarex)), wind ([windrex](https://github.com/Net-Zero-Horizon/windrex)), and OTEC ([OTEX](https://github.com/Net-Zero-Horizon/OTEX)) availability profiles.
- **Plugin system** — Directory-based plugins with simulation lifecycle hooks, GUI integration, and Julia overlay modules for custom constraints.
- **CLI** — `run`, `validate`, `export`, `studio`, `precompile`, `info` and `plugin` commands (plus `train-demand-model` / `build-demand-dataset` demand-data utilities) with Rich formatting and progress tracking.
- **HDF5 output** — Structured results with derived metrics (LCOE, VALCOE, capacity factor) exportable to CSV, Excel, and JSON.

---

## Feature Comparison

| Feature | ESFEX | PyPSA | GenX | Calliope | TIMES | OSeMOSYS |
|---------|:-----:|:-----:|:----:|:--------:|:-----:|:--------:|
| Capacity expansion | ● | ● | ● | ● | ● | ● |
| Operational dispatch (hourly) | ● | ● | ● | ● | Time slices | Time slices |
| Two-stage decomposition | ● | ○ | ○ | ○ | ○ | ○ |
| Rolling horizon dispatch | ● | ● | ○ | ● | ○ | ○ |
| DC power flow (KCL/KVL) | ● | ● | ○ | ○ | ○ | ○ |
| AC optimal power flow | ● | ◐* | ○ | ○ | ○ | ○ |
| Battery cyclic SOC | ● | ● | ● | ● | Simplified | Simplified |
| EV fleet modeling (V2G) | ● | Limited | ○ | ○ | ● | ○ |
| Primary energy supply chain | ● | Limited | ○ | Limited | ● | Partial |
| Electrolyzer / P2H₂ | ● | ● | ● | ● | ● | Limited |
| Stochastic programming | ● | ● | ○ | ○ | ● | ○ |
| N-1 security constraints | ● | ● | ○ | ○ | ○ | ○ |
| MGA / near-optimal | MGA + SPORES | MGA | MGA | SPORES | ○ | ○ |
| Sobol sensitivity | ● | ○ | ○ | ○ | ○ | ○ |
| GIS-based Studio | ● | ○ | ○ | ○ | ○ | ○ |
| Plugin / extension system | ● | ○ | ○ | ○ | ○ | ○ |
| Solver backend | JuMP | Linopy | JuMP | Pyomo | GAMS | GLPK/CBC |

<sub>● full support · ◐ partial · ○ not supported. *PyPSA performs an AC power flow via Newton-Raphson, not a full ACOPF. See [`docs/index.md`](docs/index.md) for the extended comparison and citations.</sub>

---

## Installation

ESFEX is a hybrid Python/Julia package. Python ≥ 3.10 and a working Julia ≥ 1.9 installation are required; the Julia dependencies are managed automatically through `juliacall` on first run.

### Windows installer (no Python/Julia required)

For Windows users who don't want to manage a Python/Julia toolchain, a native
`.exe` installer bundles everything (Python, Qt, Julia, the GDAL stack) and adds
an **"ESFEX Studio"** Start Menu shortcut — no `pip`, no `PATH` setup. Download
it from the [latest release](https://github.com/Net-Zero-Horizon/ESFEX/releases/latest)
(`ESFEX-<version>-Windows-x86_64.exe`). Build details: [`installer/`](installer/).

### From PyPI

```bash
pip install esfex
```

### Conda / Mamba

Create an environment where conda-forge supplies the native dependencies (Qt,
the Julia bridge, HDF5, BLAS) and ESFEX is installed from PyPI on top:

```bash
conda env create -f environment.yml   # or: mamba env create -f environment.yml
conda activate esfex
esfex info
```

### From source (development mode)

```bash
git clone https://github.com/Net-Zero-Horizon/ESFEX.git
cd ESFEX
pip install -e .
```

The GIS Studio (PySide6) is included in the core install — no extra is required.

### Windows: if `esfex` is "not recognized"

`esfex` is a console script that pip installs into your environment's
`Scripts\` folder. **pip does not modify `PATH`** — if that folder is not
already on `PATH`, the `esfex` command will not be found (pip prints a yellow
*"installed in '…\Scripts' which is not on PATH"* warning). This is common on
Windows when Python was installed without **"Add Python to PATH"**, when the
install fell back to a per-user location (`%AppData%\Roaming\Python\…\Scripts`),
or with the Microsoft Store build of Python.

The robust, `PATH`-independent way to launch ESFEX is to run it as a module —
this only needs `python` itself on `PATH`:

```bash
python -m esfex studio          # equivalent to: esfex studio
python -m esfex run -c my_system.yaml
```

Alternatively, install into a virtual environment and **activate it** (then
`Scripts\` is on `PATH` for that shell), and remember that `PATH` changes are
only picked up by **newly opened** terminals:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install esfex
esfex studio
```

### Optional dependency groups

All runtime features — visualization, sensitivity analysis, resource
workflows, benchmarking, and the ML/DL demand models — ship as **core
dependencies**, so a plain `pip install esfex` already includes them.
The only optional group is the developer tooling:

```bash
pip install -e ".[dev]"          # pytest, pytest-cov, ruff, black, mypy
```

### Julia backend

The Julia optimization models live in [`src/esfex/julia/`](src/esfex/julia/) with their own `Project.toml`. On the first `esfex run`, `juliacall` instantiates the Julia environment automatically. To build a sysimage for faster startup:

```bash
esfex precompile
```

### Solvers

ESFEX supports ten solver backends, selectable per run (`--solver`) or in the config: **HiGHS** (default), CBC, GLPK, Gurobi, CPLEX, SCIP, and Xpress for LP/MIP problems; Clarabel and SCS for conic relaxations; and Ipopt for the nonlinear ACOPF formulations.

Only the **open-source** solvers are bundled (HiGHS, GLPK, Clarabel, SCS,
Ipopt). The **commercial** solvers (Gurobi, CPLEX, Xpress) are *not* installed by
default — they require a license that is the user's responsibility. They remain
selectable: install the corresponding Julia package into the ESFEX Julia
environment and ESFEX loads it on demand, e.g.

```julia
# with a valid license/GRB_LICENSE_FILE already configured
using Pkg; Pkg.activate(joinpath(dirname(pathof(ESFEX)))); Pkg.add("Gurobi")
```

This keeps the default install smaller and free of license-locked binaries.

---

## Quick Start

```bash
# Validate a configuration file
esfex validate -c my_system.yaml

# Run a 25-year capacity expansion + dispatch simulation
esfex run -c my_system.yaml --years 25 --verbose

# Run in unit-commitment (MIP) mode with a specific solver
esfex run -c my_system.yaml --mode unit_commitment --solver gurobi

# Export results to CSV
esfex export -r results/output.h5 -f csv

# Show version and system information
esfex info
```

### Python API

```python
from esfex import load_config
from esfex.runner import Orchestrator

config = load_config("my_system.yaml")
orchestrator = Orchestrator(config, output_dir="./results")
results = orchestrator.run(years=25)

for year in results:
    print(f"Year {year.year}: RE={year.re_penetration:.1%}, "
          f"Cost=${year.objective:,.0f}")
```

---

## The Studio

<p align="center">
  <img src="docs/assets/studio-screenshot.png" alt="ESFEX Studio — GIS-based power system designer" width="900"/>
</p>

ESFEX ships with an interactive, map-based **Studio** for building and editing power-system configurations visually instead of hand-writing YAML.

```bash
esfex studio                     # start from a blank canvas
esfex studio -c my_system.yaml   # open an existing configuration
```

> On Windows, if `esfex` is "not recognized", launch it as a module instead:
> `python -m esfex studio`. See [Installation → Windows](#windows-if-esfex-is-not-recognized).

Place nodes, generators, batteries, and transmission lines directly on a Leaflet map with geographic routing, edit element parameters through validated forms, and run resource-assessment wizards (rooftop solar, utility PV via [solarex](https://github.com/Net-Zero-Horizon/solarex), wind via [windrex](https://github.com/Net-Zero-Horizon/windrex), OTEC via [OTEX](https://github.com/Net-Zero-Horizon/OTEX)) to generate availability profiles. The Studio writes standard ESFEX YAML that the CLI and Python API consume unchanged.

---

## Configuration

ESFEX is driven by a single YAML configuration describing the system topology, technologies, temporal settings, and solver options. Key sections:

| Section | Purpose |
|---------|---------|
| `simulation_mode` | `development`, `economic_dispatch`, or `unit_commitment` |
| `temporal` | Resolution, rolling-horizon window/overlap, investment resolution |
| `solver` | Solver name, threads, gap, time limit, numerical options |
| `nodes` / `buses` | Network topology and demand assignment |
| `generators` | Thermal, renewable, and conversion technologies |
| `batteries` | Storage with degradation and duration bounds |
| `transmission` | Lines, transformers, converters; DC/AC power flow settings |
| `development_zones` | Candidate sites for new generation investment |

See the [Configuration Reference](docs/reference/config-reference.md) and the [User Guide](docs/user-guide/configuration.md) for the full schema.

---

## Project Structure

```
ESFEX/
├── src/esfex/
│   ├── cli.py                  # Typer CLI entry point
│   ├── runner.py               # Orchestrator (two-stage run loop)
│   ├── config/                 # Pydantic schema + YAML loader
│   ├── bridge/                 # Python↔Julia bridge (juliacall adapters)
│   ├── julia/                  # Julia optimization models (JuMP)
│   │   └── src/ESFEX.jl        # Power system, master problem, AC/DC flow, …
│   ├── models/                 # EV, rooftop solar, demand estimation
│   ├── io/                     # Demand loading, HDF5/CSV/Excel export
│   ├── topology/               # Network construction and reduction
│   ├── sensitivity/            # Sobol / sensitivity analysis
│   ├── analysis/               # Post-processing and derived metrics
│   ├── visualization/          # PySide6 GIS Studio + result charts
│   ├── plugins/                # Plugin framework and discovery
│   └── paths.py                # Central data-path registry
├── tests/                      # Test suite (pytest)
├── docs/                       # MkDocs documentation
├── mkdocs.yml                  # Documentation site config
└── pyproject.toml              # Package + dependency configuration
```

---

## Documentation

Full documentation is built with MkDocs and lives under [`docs/`](docs/).

| Section | Description |
|---------|-------------|
| [Getting Started](docs/getting-started/installation.md) | Installation, quickstart, architecture, core concepts |
| [Tutorials](docs/tutorials/single-system.md) | Single-system, multi-node, EV, stochastic, sensitivity |
| [User Guide](docs/user-guide/cli.md) | CLI, configuration, master problem, data formats |
| [GUI Editor](docs/gui/overview.md) | Interactive map-based grid editor (Studio) |
| [Mathematical Formulation](docs/formulation/overview.md) | Master problem, dispatch, DC/AC flow, primary energy, electrolyzer |
| [API Reference](docs/api/index.md) | Python and Julia public API |
| [Reference](docs/reference/config-reference.md) | Config fields, HDF5 schema, constraint catalog, glossary |

To serve the docs locally:

```bash
pip install mkdocs-material
mkdocs serve
```

---

## Requirements

- **Python** ≥ 3.10 (3.10, 3.11, 3.12 supported)
- **Julia** ≥ 1.9 (managed via `juliacall`)
- Core Python: NumPy, Pandas, SciPy, h5py, Pydantic, NetworkX, Typer, Rich, PySide6
- A supported solver: HiGHS (default, open-source), or Gurobi / CPLEX / CBC / GLPK / SCIP / Xpress / Clarabel / SCS / Ipopt

---

## Citation

If you use ESFEX in academic work, please cite:

```bibtex
@software{esfex2026,
  title   = {ESFEX: Energy System FlEXibility — Power System Optimization},
  author  = {Soto Calvo, Manuel and Lee, Han Soo},
  year    = {2026},
  url     = {https://github.com/Net-Zero-Horizon/ESFEX},
  version = {0.1.3},
  license = {Apache-2.0}
}
```

---

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) for the requirements for acceptable contributions (coding standard, tests, and the pull-request process), with [Development Setup](docs/contributing/development-setup.md) for the development environment and [Testing](docs/contributing/testing.md) for the test workflow. Bug reports and feature requests go to the [GitHub issue tracker](https://github.com/Net-Zero-Horizon/ESFEX/issues).

---

## License

ESFEX is released under the **Apache License 2.0** — see [LICENSE](LICENSE) for the full text.
