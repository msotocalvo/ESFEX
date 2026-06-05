# Installation


## Prerequisites

- **Python 3.10 or newer** (3.11 or 3.12 recommended). Python 3.13 has not yet been tested.
- **pip 21.0+** or **conda** package manager.
- **Julia 1.9+** -- automatically managed by `juliacall` on first run if not already installed. If you prefer to manage Julia manually, install it from [julialang.org](https://julialang.org/downloads/).
- **At least 4 GB of free RAM** for typical simulations. Large multi-node, multi-year studies may require 8--16 GB.
- **At least 2 GB of free disk space** for the Julia environment, compiled sysimage, and solver packages.

### Checking Python Version

```bash
python --version
# Expected: Python 3.10.x, 3.11.x, or 3.12.x
```

If `python` points to Python 2, try `python3 --version` instead. On Windows, you may also use `py -3 --version`.

## Basic Installation

Install the core package from PyPI:

```bash
pip install esfex
```

This installs the core dependencies:

| Package | Purpose |
|---------|---------|
| NumPy >= 1.24 | Array operations for demand, generation, and result data |
| Pandas >= 2.0 | Tabular data handling for demand files and summaries |
| h5py >= 3.8 | HDF5 file reading and writing for simulation results |
| Pydantic >= 2.0 | Configuration validation and schema enforcement |
| PyYAML >= 6.0 | YAML configuration file parsing |
| Typer >= 0.9 | Command-line interface framework |
| juliacall >= 0.9.14 | Python-Julia interoperability bridge |
| NetworkX >= 3.0 | Graph algorithms for network topology analysis |
| OpenPyXL >= 3.1 | Excel file reading for demand data |
| Rich >= 13.0 | Terminal formatting, progress bars, and tables |
| psutil >= 5.9 | System resource monitoring during optimization |
| SciPy >= 1.10 | Sparse matrix operations and scientific computing utilities |

## Installation with Extras

Optional dependency groups enable additional functionality. Combine multiple extras with commas inside the brackets.

=== "Visualization"

    ```bash
    pip install "esfex[viz]"
    ```

    Adds **Matplotlib** (>= 3.7) and **Plotly** (>= 5.14) for result visualization, dispatch stack plots, and interactive charts. Required if you want to generate publication-quality figures from simulation results.

=== "Studio"

    ```bash
    pip install esfex
    ```

    Adds **PySide6** (>= 6.5) for the interactive GIS-based grid editor. The GUI allows you to place nodes, generators, batteries, and transmission lines on an OpenStreetMap-based map, configure equipment parameters through forms, and export the system as a YAML configuration file.

=== "Sensitivity Analysis"

    ```bash
    pip install "esfex[sensitivity]"
    ```

    Adds **SALib** (>= 1.4.7) and **Matplotlib** for Sobol global sensitivity analysis. This enables systematic exploration of how input parameter uncertainty affects investment decisions and system costs.

=== "Resource Workflows"

    ```bash
    pip install "esfex[workflows]"
    ```

    Adds **pvlib**, **geopandas**, **duckdb**, **atlite**, **rasterio**, **scikit-learn**, and supporting libraries for solar PV, wind, and OTEC resource assessment wizards. These wizards generate hourly availability profiles from geographic and meteorological data.

=== "Full Installation"

    ```bash
    pip install "esfex[viz,sensitivity,workflows]"
    ```

    Installs all optional features.

## Development Installation

For contributing to ESFEX or running from source:

```bash
# Clone the repository
git clone https://github.com/Net-Zero-Horizon/ESFEX.git
cd esfex

# Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

# Install in editable mode with development dependencies
pip install -e ".[dev,viz,sensitivity]"
```

The `dev` extras include:

| Package | Purpose |
|---------|---------|
| pytest >= 7.0 | Test runner |
| pytest-cov >= 4.0 | Code coverage reporting |
| black >= 23.0 | Code formatting |
| ruff >= 0.1 | Fast linting |
| mypy >= 1.0 | Static type checking |

### Running Tests

```bash
# Run all tests
pytest

# Run tests with coverage report
pytest --cov=esfex --cov-report=html

# Skip tests that require Julia (faster feedback during Python-only changes)
pytest -m "not julia"
```

## Julia Setup

ESFEX uses Julia for its optimization backend via the `juliacall` Python package. Julia management is mostly automatic.

### First Run Behavior

1. **Julia installation**: If Julia is not found on your PATH, `juliacall` downloads and installs a compatible version automatically.
2. **Package installation**: The `ESFEX.jl` module and its dependencies (JuMP, HiGHS, Graphs, etc.) are installed into a dedicated Julia environment.
3. **Compilation**: Julia compiles the module on first use. This takes **2--5 minutes** depending on your hardware.
4. **Subsequent runs**: The compiled module is cached. Startup drops to a few seconds.

You can trigger the first-run compilation without running a simulation by executing:

```bash
esfex info
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PYTHON_JULIACALL_THREADS` | `auto` | Number of Julia threads for parallel operations |
| `JULIA_NUM_THREADS` | `auto` | Alternative thread configuration (juliacall takes precedence) |
| `JULIA_DEPOT_PATH` | `~/.julia` | Location where Julia packages and compiled artifacts are stored |
| `PYTHON_JULIACALL_HANDLE_SIGNALS` | `yes` | Whether juliacall installs its own signal handlers |

To explicitly set Julia threads for a large model:

```bash
PYTHON_JULIACALL_THREADS=8 esfex run -c config.yaml --years 25
```

### Julia Packages

ESFEX.jl depends on the following Julia packages (installed automatically on first run):

- **JuMP** (>= 1.0) -- Optimization modeling language for defining variables, constraints, and objectives
- **HiGHS** -- Open-source LP/MIP solver, the default backend
- **Graphs** -- Graph algorithms used for network topology analysis and cycle detection in DC power flow
- **LinearAlgebra**, **SparseArrays** -- Matrix operations for constraint construction
- **Printf** -- Formatted output for solution summaries

## Solver Setup

### HiGHS (Default)

HiGHS is bundled with the Julia `HiGHS.jl` package and requires no additional setup. It supports LP, MIP, and QP problems. For most ESFEX simulations (single-system islands with fewer than 10 nodes), HiGHS provides excellent performance.

### Commercial Solvers (Optional)

For larger models or faster solve times, install a commercial solver. The solver is selected via the `--solver` CLI flag or the `solver.name` configuration field.

=== "Gurobi"

    Gurobi is the fastest commercial solver for LP and MIP problems. Academic licenses are available for free.

    1. Install Gurobi and obtain a license from [gurobi.com](https://www.gurobi.com/)
    2. Set the `GRB_LICENSE_FILE` environment variable to point to your license file
    3. Install the Julia package:
    ```julia
    using Pkg
    Pkg.add("Gurobi")
    ```
    4. Run ESFEX with:
    ```bash
    esfex run -c config.yaml -s gurobi
    ```

=== "CPLEX"

    IBM ILOG CPLEX is available through academic initiatives or commercial licenses.

    1. Install IBM ILOG CPLEX from [ibm.com](https://www.ibm.com/products/ilog-cplex-optimization-studio)
    2. Ensure the `CPLEX_STUDIO_BINARIES` environment variable is set
    3. Install the Julia package:
    ```julia
    using Pkg
    Pkg.add("CPLEX")
    ```
    4. Run ESFEX with:
    ```bash
    esfex run -c config.yaml -s cplex
    ```

=== "CBC"

    CBC (Coin-or branch and cut) is a free open-source MIP solver. Slower than HiGHS for most problems but useful as a fallback.

    ```julia
    using Pkg
    Pkg.add("Cbc")
    ```
    Run with:
    ```bash
    esfex run -c config.yaml -s cbc
    ```

=== "GLPK"

    GLPK (GNU Linear Programming Kit) is a free open-source LP/MIP solver.

    ```julia
    using Pkg
    Pkg.add("GLPK")
    ```
    Run with:
    ```bash
    esfex run -c config.yaml -s glpk
    ```

## Verifying Installation

```bash
esfex info
```

Expected output for a fresh installation:

```
ESFEX version 0.1.3
Python: 3.12.0
Julia: Available via juliacall

Available solvers (Julia/JuMP):
  HiGHS: Available (v1.7.0)
  Gurobi: Not found
  CPLEX: Not found
```

You can also verify that the Python package imported correctly:

```python
python -c "import esfex; print(esfex.__version__)"
# Expected: 0.1.3
```

To verify the Julia backend is functional, run a quick validation:

```bash
esfex validate -c examples/minimal.yaml
```

Expected output:

```
Validating: examples/minimal.yaml
Configuration is valid!
┌──────────────────────┬────────────────┐
│ Setting              │ Value          │
├──────────────────────┼────────────────┤
│ Simulation Mode      │ development    │
│ Solver               │ highs          │
│ Systems              │ island         │
│   island nodes       │ 1              │
│   island generators  │ 2              │
│   island batteries   │ 1              │
└──────────────────────┴────────────────┘
```

## Platform-Specific Notes

### Linux (Ubuntu / Debian)

Linux is the recommended platform for ESFEX. No special configuration is required for the core package.

**GUI dependencies**: The PySide6-based Studio requires X11 or Wayland display libraries. If the editor fails to launch:

```bash
# Ubuntu / Debian
sudo apt install libxcb-xinerama0 libxkbcommon-x11-0 libegl1 libxcb-cursor0

# Fedora / RHEL
sudo dnf install libxcb libxkbcommon-x11 mesa-libEGL
```

**Headless servers**: If you are running on a server without a display (e.g., for batch simulations), the GUI is not needed. Install only the core package or use `esfex[viz]` for generating figures without the interactive editor.

### macOS

ESFEX works on macOS 12 (Monterey) and newer, on both Intel and Apple Silicon (M1/M2/M3) hardware.

**Julia on Apple Silicon**: The `juliacall` package handles architecture selection automatically. If you encounter issues, ensure you are using a native ARM64 Python build (not Rosetta-emulated x86):

```bash
python -c "import platform; print(platform.machine())"
# Expected on Apple Silicon: arm64
```

**PySide6 on macOS**: The GUI may prompt for accessibility permissions on first launch. Grant these permissions in System Settings > Privacy & Security > Accessibility.

### Windows

ESFEX is fully supported on Windows 10 and Windows 11. Use either the standard Python installer from python.org or Anaconda/Miniconda.

**UTF-8 encoding**: ESFEX automatically configures UTF-8 output on Windows. If you still encounter encoding errors in your terminal, set the environment variable before running:

```powershell
$env:PYTHONIOENCODING = "utf-8"
```

Or in Command Prompt:

```cmd
set PYTHONIOENCODING=utf-8
```

**Long path support**: On Windows, enable long path support if you encounter path length errors during Julia package installation:

1. Open the Group Policy Editor (`gpedit.msc`)
2. Navigate to Computer Configuration > Administrative Templates > System > Filesystem
3. Enable "Enable Win32 long paths"

Alternatively, set the registry key:

```powershell
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
```

**Windows Subsystem for Linux (WSL)**: Running ESFEX inside WSL2 with Ubuntu provides the same experience as native Linux and is often the easiest path on Windows. Follow the Linux instructions after setting up WSL.

---


## Troubleshooting


### juliacall Compilation Errors

```bash
# Clear the Julia environment and retry
rm -rf ~/.julia/environments/pyjuliapkg
esfex info
```

If the error persists, try clearing the entire Julia depot (this forces a full reinstall of all Julia packages):

```bash
rm -rf ~/.julia
esfex info
```

### juliacall Hangs on First Run

On some systems, the initial Julia package precompilation can appear to hang for 5--10 minutes with no output. This is normal for the first invocation. If it exceeds 15 minutes, check for:

- Insufficient disk space (Julia needs approximately 1--2 GB for packages and compilation artifacts)
- Antivirus software blocking Julia compilation (common on Windows)
- Network connectivity issues (Julia downloads packages from the internet on first run)

### PySide6 Platform Plugin (Linux)

If the Studio fails to launch on Linux with an error like `qt.qpa.plugin: Could not load the Qt platform plugin "xcb"`:

```bash
# Install required Qt platform dependencies
sudo apt install libxcb-xinerama0 libxkbcommon-x11-0 libegl1 libxcb-cursor0

# If the issue persists, try setting the platform explicitly
export QT_QPA_PLATFORM=xcb
esfex studio
```

### PySide6 Wayland Issues (Linux)

On Wayland-based desktops (e.g., GNOME on Fedora), PySide6 may have rendering issues. Force X11 mode:

```bash
export QT_QPA_PLATFORM=xcb
esfex studio
```

### Windows UTF-8 Encoding

ESFEX automatically configures UTF-8 output on Windows. If you encounter encoding errors, set:

```powershell
$env:PYTHONIOENCODING = "utf-8"
```

### HiGHS Solver Not Found

If `esfex info` reports that HiGHS is not available, the Julia environment may be incomplete:

```bash
# Force reinstallation of HiGHS in Julia
python -c "
from juliacall import Main as jl
jl.seval('using Pkg; Pkg.add(\"HiGHS\")')
"
```

### Out of Memory During Optimization

Large models (many nodes, many years, small temporal resolution) can consume significant memory. Options:

1. **Increase rolling horizon window overlap** -- reduces the size of each subproblem
2. **Reduce the number of representative days** in the master problem (`master_problem.representative_days_per_year`)
3. **Use a coarser temporal resolution** (`temporal.resolution_hours: 2` or higher)
4. **Reduce the number of simulation years** as a first test

### Import Errors After Upgrade

If you upgrade ESFEX and encounter import errors:

```bash
# Reinstall with dependencies
pip install --force-reinstall esfex

# Clear Julia cached environment
rm -rf ~/.julia/environments/pyjuliapkg
```

### Firewall Blocking Julia Package Downloads

If Julia cannot download packages on first run due to firewall restrictions, you can pre-install Julia packages manually in an environment where internet access is available, then copy the `~/.julia` directory to the target machine.

## Next Steps

- [Quickstart](quickstart.md) — run your first simulation
- [Core Concepts](concepts.md) — understand the optimization model
- [Configuration Guide](../user-guide/configuration.md) — full YAML reference
