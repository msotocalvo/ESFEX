# Development Setup

## Prerequisites

| Requirement | Version | Purpose |
|-------------|---------|---------|
| Python | >= 3.10 | Runtime |
| Julia | >= 1.9 | Optimization backend |
| Git | Any recent | Version control |
| pip | >= 22.0 | Package management |


---


## Clone the Repository

```bash
git clone https://github.com/your-org/esfex.git
cd esfex
```


---


## Python Environment

Virtual environment setup:

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# or
.venv\Scripts\activate     # Windows
```

Install in development mode:

```bash
pip install -e ".[dev]"
```

Included dependencies:

- **Core**: numpy, pandas, scipy, h5py, pyyaml, pydantic, juliacall, typer, rich
- **Visualization**: matplotlib, plotly
- **GUI**: PySide6, PySide6-WebEngine
- **Sensitivity**: SALib
- **Dev tools**: pytest, pytest-cov, ruff, mypy, pre-commit


---


## Julia Environment

Julia initializes automatically on first run. Manual setup:

```bash
# From the project root
julia --project=src/esfex/julia -e 'using Pkg; Pkg.instantiate()'
```

### Julia Dependencies

The Julia `Project.toml` includes:

- **JuMP** --- Mathematical programming framework
- **HiGHS** --- Default LP/MIP solver
- **LinearAlgebra** --- Matrix operations

Optional solvers:

```julia
# In Julia REPL
using Pkg
Pkg.add("Gurobi")   # Requires Gurobi license
Pkg.add("CPLEX")    # Requires CPLEX license
```


---


## System Image (Optional)

Build a Julia system image for faster startup:

```bash
esfex info --build-sysimage
```

Precompiles all Julia dependencies into a native image, reducing first-call latency from ~30s to ~2s.


---


## IDE Setup

### VS Code

Recommended extensions:

- **Python** (ms-python.python)
- **Julia** (julialang.language-julia)
- **YAML** (redhat.vscode-yaml)
- **Ruff** (charliermarsh.ruff)

Settings (`.vscode/settings.json`):

```json
{
    "python.defaultInterpreterPath": ".venv/bin/python",
    "python.analysis.typeCheckingMode": "basic",
    "[python]": {
        "editor.defaultFormatter": "charliermarsh.ruff"
    },
    "julia.executablePath": "/usr/local/bin/julia"
}
```

#### Debugging Configuration

`.vscode/launch.json` debugging configurations:

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "ESFEX: Run Simulation",
            "type": "debugpy",
            "request": "launch",
            "module": "esfex.cli",
            "args": ["run", "-c", "${workspaceFolder}/configs/example.yaml"],
            "cwd": "${workspaceFolder}",
            "env": {
                "ESFEX_LOG_LEVEL": "DEBUG",
                "ESFEX_JULIA_THREADS": "4"
            },
            "justMyCode": false
        },
        {
            "name": "ESFEX: GUI Editor",
            "type": "debugpy",
            "request": "launch",
            "module": "esfex.cli",
            "args": ["editor", "-c", "${workspaceFolder}/configs/example.yaml"],
            "cwd": "${workspaceFolder}",
            "justMyCode": false
        },
        {
            "name": "ESFEX: Run Tests",
            "type": "debugpy",
            "request": "launch",
            "module": "pytest",
            "args": [
                "tests/",
                "-v",
                "--tb=short",
                "-x"
            ],
            "cwd": "${workspaceFolder}",
            "justMyCode": false
        },
        {
            "name": "ESFEX: Debug Single Test",
            "type": "debugpy",
            "request": "launch",
            "module": "pytest",
            "args": [
                "${file}",
                "-v",
                "--tb=long",
                "-x",
                "--no-header"
            ],
            "cwd": "${workspaceFolder}",
            "justMyCode": false
        },
        {
            "name": "ESFEX: Sensitivity Analysis",
            "type": "debugpy",
            "request": "launch",
            "module": "esfex.cli",
            "args": [
                "sensitivity",
                "-c", "${workspaceFolder}/configs/example.yaml",
                "--samples", "64"
            ],
            "cwd": "${workspaceFolder}",
            "justMyCode": false
        }
    ]
}
```

Set breakpoints in `runner.py` or `adapters.py` to inspect the simulation loop. `justMyCode: false` enables stepping into library code (JuliaCall, Pydantic, etc.).

#### Tasks Configuration

`.vscode/tasks.json` for common commands:

```json
{
    "version": "2.0.0",
    "tasks": [
        {
            "label": "Lint (ruff)",
            "type": "shell",
            "command": "ruff check src/ tests/",
            "problemMatcher": []
        },
        {
            "label": "Format (ruff)",
            "type": "shell",
            "command": "ruff format src/ tests/",
            "problemMatcher": []
        },
        {
            "label": "Type check (mypy)",
            "type": "shell",
            "command": "mypy src/esfex/",
            "problemMatcher": []
        },
        {
            "label": "Build Julia sysimage",
            "type": "shell",
            "command": "python -m esfex.cli info --build-sysimage",
            "problemMatcher": []
        }
    ]
}
```

### PyCharm

1. Set Python interpreter to `.venv/bin/python`
2. Install Julia plugin for `.jl` file support
3. Configure Ruff as the external formatter
4. Create a Run Configuration for `esfex.cli` with module mode


---


## Project Structure

```
esfex/
├── src/esfex/
│   ├── __init__.py              # Package exports
│   ├── cli.py                   # CLI (Typer)
│   ├── runner.py                # Simulation orchestrator
│   ├── config/
│   │   ├── schema.py            # Pydantic models
│   │   ├── loader.py            # YAML loading
│   │   └── solver.py            # Solver configuration
│   ├── bridge/
│   │   ├── adapters.py          # Python→Julia adapters
│   │   └── julia_setup.py       # Julia initialization
│   ├── io/
│   │   ├── demand.py            # Demand data loading
│   │   └── exporter.py          # Results export
│   ├── models/
│   │   ├── ev.py                # EV fleet modeling
│   │   └── solar_rooftop.py     # Rooftop PV modeling
│   ├── plugins/
│   │   ├── __init__.py          # Public API
│   │   ├── protocol.py          # ESFEXPlugin base class
│   │   ├── manager.py           # PluginManager singleton
│   │   └── availability_generator/  # Built-in plugin
│   ├── sensitivity/
│   │   └── engine.py            # Sobol sensitivity analysis
│   ├── julia/
│   │   ├── src/
│   │   │   ├── ESFEX.jl        # Julia module entry
│   │   │   ├── types.jl         # Type definitions
│   │   │   ├── power_system.jl  # Operational dispatch
│   │   │   ├── master_problem.jl # Capacity expansion
│   │   │   ├── transmission_dc.jl # DC power flow
│   │   │   ├── primary_energy.jl # Fuel supply chain
│   │   │   └── electrolyzer.jl  # P2H2 modeling
│   │   └── Project.toml         # Julia dependencies
│   ├── utils/
│   │   ├── helpers.py           # Boundary conditions, utilities
│   │   └── temporal.py          # Rolling horizon, aggregation
│   └── visualization/           # Studio
│       ├── app.py               # Application entry
│       ├── main_window.py       # Main window
│       ├── map_widget.py        # Leaflet map
│       ├── data/
│       │   ├── gui_model.py     # Data model
│       │   └── serializer.py    # YAML ↔ GUI conversion
│       ├── panels/              # Property forms (~24 files)
│       ├── workflows/           # Analysis wizards
│       └── resources/           # HTML, CSS, JS assets
├── tests/
│   ├── test_config.py
│   ├── test_runner.py
│   ├── test_adapters.py
│   ├── test_plugins.py
│   └── ...
├── docs/                        # Documentation (MkDocs)
├── configs/                     # Example configurations
├── pyproject.toml               # Package configuration
└── mkdocs.yml                   # Documentation config
```


---


## Code Style

### Python

ESFEX uses **Ruff** for linting and formatting:

```bash
# Lint
ruff check src/

# Format
ruff format src/

# Auto-fix
ruff check --fix src/
```

Key style rules:

- Line length: 100 characters
- Imports: sorted by isort (built into ruff)
- Docstrings: Google style
- Type hints: encouraged for public API

### Julia

Julia code follows standard conventions:

- 4-space indentation
- `snake_case` for functions and variables
- `PascalCase` for types
- `UPPER_CASE` for constants
- Document functions with docstrings


---


## Pre-commit Hooks

Install hooks:

```bash
pre-commit install
```

Hooks run automatically on `git commit`:

1. **ruff** --- Lint and format Python code
2. **mypy** --- Type checking (optional)
3. **trailing-whitespace** --- Remove trailing spaces
4. **end-of-file-fixer** --- Ensure newline at EOF


---


## Common Development Workflows

### Adding a New Configuration Option

1. Add the Pydantic field in `src/esfex/config/schema.py`
2. Set a sensible default value so existing configs remain valid
3. Wire the field in `runner.py` or the relevant adapter
4. Add a test in `tests/test_schema.py` for both the default and explicit cases
5. Update the example configuration in `configs/`

```python
# schema.py — example
class PenaltiesConfig(BaseModel):
    loss_of_load: float = 10e6
    max_curtailment_ratio: float = 0.05
    my_new_penalty: float = 1000.0  # Add with default
```

### Modifying the Julia Backend

1. Edit the relevant `.jl` file (see [Julia Development](julia-development.md))
2. Test from the Julia REPL first to catch syntax errors immediately
3. Run the Python integration through the adapter
4. Check that the Python-Julia bridge serializes new fields correctly

### Running a Quick End-to-End Test

```bash
# Run a minimal 1-year single-node simulation
esfex run -c configs/single_node_test.yaml --years 1

# Check the output
ls output/
python -c "import h5py; f = h5py.File('output/results.h5', 'r'); print(list(f.keys()))"
```

### Debugging Julia from Python

Julia error tracebacks can be opaque when propagated to Python. Wrap calls for diagnostics:

```python
# In adapters.py or runner.py, wrap Julia calls for better diagnostics
from esfex.bridge.julia_setup import get_esfex_module

jl = get_esfex_module()
try:
    result = jl.create_power_system(input_data)
except Exception as e:
    # Print the full Julia backtrace
    print(f"Julia error: {e}")
    # Write the model to file for offline inspection
    jl.write_to_file(model, "/tmp/debug_model.lp")
    raise
```

### Working with HDF5 Output

```python
import h5py
import numpy as np

with h5py.File("output/results_system.h5", "r") as f:
    # List all groups
    def print_tree(name, obj):
        print(name)
    f.visititems(print_tree)

    # Read generation data for year 1
    gen = f["year_1/generation"][:]
    print(f"Shape: {gen.shape}")  # (generators, nodes, hours)
    print(f"Total: {gen.sum():.1f} MWh")
```


---


## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ESFEX_JULIA_THREADS` | Julia thread count | 4 |
| `ESFEX_SOLVER` | Override default solver | `highs` |
| `ESFEX_SYSIMAGE` | Path to Julia system image | None |
| `ESFEX_LOG_LEVEL` | Logging level | `INFO` |
| `ESFEX_PLUGIN_PATH` | Extra plugin search directories (colon-separated) | None |


---


## Troubleshooting

### Julia Not Found

```bash
# Check Julia is in PATH
which julia
julia --version

# If using pyenv/conda, ensure Julia is accessible
export PATH="$HOME/.julia/juliaup/bin:$PATH"
```

### PySide6 Issues (Linux)

```bash
# Install system dependencies
sudo apt install libxcb-xinerama0 libxkbcommon-x11-0
# Or for Qt WebEngine:
sudo apt install libnss3 libxcomposite1 libxdamage1 libxrandr2
```

### Solver Licensing

Commercial solvers require license files:

```bash
# Gurobi
export GRB_LICENSE_FILE=/path/to/gurobi.lic

# CPLEX
export CPLEX_STUDIO_DIR=/opt/ibm/ILOG/CPLEX_Studio
```

### JuliaCall Import Errors

Troubleshooting `juliacall` import or connection failures:

```bash
# Ensure Julia is the correct version
julia --version  # Must be >= 1.9

# Rebuild juliacall's Julia environment
python -c "import juliacall; print(juliacall.Main)"

# If still failing, remove the cached environment
rm -rf ~/.julia/environments/pyjuliapkg/
pip install -e ".[dev]"  # Reinstall to regenerate
```

### Out-of-Memory During Simulation

Mitigation strategies for large multi-node, multi-year simulations:

```bash
# Reduce Julia threads (each thread has its own model copy)
export ESFEX_JULIA_THREADS=2

# Use a smaller rolling horizon window in config
# temporal.window_hours: 48  (instead of 168)

# Enable garbage collection hints
export JULIA_GC_ALLOC_PERIOD=1
```
