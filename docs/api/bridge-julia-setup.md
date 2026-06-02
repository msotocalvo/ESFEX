# Julia Setup

Module: `esfex.bridge.julia_setup`

## Architecture

The module uses two global singletons to cache the Julia runtime and the loaded ESFEX module:

```python
_julia_instance = None   # juliacall Main module handle
_esfex_module = None     # ESFEX Julia module reference
```

On first access (via `get_julia()` or any adapter constructor), the module:

1. Imports `juliacall.Main` as the Julia runtime handle.
2. Activates the ESFEX Julia project environment located at `src/esfex/julia/`.
3. Includes and loads the `ESFEX.jl` module.
4. Caches both references for subsequent calls.

Subsequent calls return the cached instances immediately, with no reinitialization overhead.

---

## Functions

### initialize_julia

```python
def initialize_julia(
    threads: int = 4,
    compile: bool = True,
    verbose: bool = False,
) -> Any
```

Initialize the Julia runtime and load the ESFEX module.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `threads` | `int` | `4` | Number of Julia threads. Sets `JULIA_NUM_THREADS` environment variable. |
| `compile` | `bool` | `True` | Whether to precompile the ESFEX module on first load. |
| `verbose` | `bool` | `False` | Enable verbose output during initialization. |

**Returns:** The `juliacall.Main` module with ESFEX loaded.

**Raises:**
- `ImportError` -- if `juliacall` is not installed.
- `RuntimeError` -- if Julia initialization or module loading fails.

**Initialization Steps:**

1. Sets `JULIA_NUM_THREADS` environment variable (uses `setdefault` to avoid overriding user-set values).
2. Resolves the Julia project path: `src/esfex/julia/Project.toml`.
3. Activates the Julia project via `Pkg.activate()`.
4. Attempts to load the ESFEX module via `include()` and `using .ESFEX`.
5. If loading fails with "invalid redefinition of constant", reuses the already-loaded module (happens during Julia session reuse).
6. If loading fails with a `LoadError` or `ArgumentError`, runs `Pkg.instantiate()` to install missing dependencies, then retries loading.
7. If `Pkg.instantiate()` fails, deletes `Manifest.toml` and retries with `Pkg.resolve(); Pkg.instantiate()`.

**Example:**

```python
from esfex.bridge.julia_setup import initialize_julia

jl = initialize_julia(threads=8, verbose=True)
# Julia runtime is now ready
```

### get_julia

```python
def get_julia() -> Any
```

Get the Julia runtime instance. Initializes Julia on first call, returns cached instance thereafter. Safe to call repeatedly.

**Returns:** The `juliacall.Main` module with ESFEX loaded.

### get_esfex_module

```python
def get_esfex_module() -> Any
```

Get the ESFEX Julia module reference.

**Returns:** The `ESFEX` Julia module (equivalent to `jl.ESFEX`).

**Raises:** `RuntimeError` if Julia is not initialized and initialization fails.

Used by adapter classes to call Julia functions:

```python
ESFEX = get_esfex_module()
model, vars = ESFEX.create_power_system(input)
```

### get_julia_path

```python
def get_julia_path() -> Path
```

Get the path to the Julia source directory.

**Returns:** `Path` pointing to `src/esfex/julia/`.

Contents:
- `Project.toml` -- Julia project dependencies.
- `Manifest.toml` -- Resolved dependency versions (auto-generated).
- `src/ESFEX.jl` -- Main module entry point.
- `src/types.jl` -- Type definitions.
- `src/power_system.jl` -- Operational dispatch model.
- `src/master_problem.jl` -- Capacity expansion model.
- `src/transmission_dc.jl` -- DC power flow constraints.
- `src/transmission_ac.jl` -- AC power flow verification.
- `src/mga.jl` -- MGA/SPORES near-optimal alternatives.
- `src/primary_energy.jl` -- Fuel supply chain model.
- `src/electrolyzer.jl` -- Electrolyzer model.

### create_julia_optimizer

```python
def create_julia_optimizer(
    solver: str = "highs",
    threads: int = 4,
    time_limit: float = 3600.0,
    gap: float = 0.01,
    verbose: bool = False,
) -> Any
```

Create a configured JuMP optimizer in Julia. Delegates to `ESFEX.create_optimizer()` for solver-specific parameter mapping and on-demand loading of solver packages.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `solver` | `str` | `"highs"` | Solver name. Options: `"highs"`, `"gurobi"`, `"cplex"`, `"scip"`, `"xpress"`, `"cbc"`, `"glpk"`. |
| `threads` | `int` | `4` | Number of solver threads. |
| `time_limit` | `float` | `3600.0` | Maximum solve time in seconds. |
| `gap` | `float` | `0.01` | MIP optimality gap tolerance. |
| `verbose` | `bool` | `False` | Enable solver output. |

**Returns:** A Julia optimizer object suitable for use with `JuMP.Model(optimizer)`.

**Solver Notes:**

| Solver | License | Install |
|--------|---------|---------|
| HiGHS | Open source (MIT) | Included by default |
| SCIP | ZIB Academic | `] add SCIP` |
| Gurobi | Commercial | `] add Gurobi` + license |
| CPLEX | Commercial | `] add CPLEX` + license |
| Xpress | Commercial | `] add Xpress` + license |
| CBC | Open source (EPL) | `] add Cbc` |
| GLPK | Open source (GPL) | `] add GLPK` |

### check_julia_available

```python
def check_julia_available() -> bool
```

Check if Julia is available without initializing it.

**Returns:** `True` if `juliacall` can be imported, `False` otherwise.

### get_julia_version

```python
def get_julia_version() -> Optional[str]
```

Get the Julia version string (e.g., `"1.10.2"`). Returns `None` if Julia is not available.

### precompile_esfex

```python
def precompile_esfex() -> None
```

Precompile the ESFEX Julia module for faster startup. Calls `Pkg.precompile()` in the ESFEX project environment.

---

## Sysimage

A Julia system image built with `PackageCompiler.jl` pre-compiles all Julia code into a shared library, reducing subsequent startup time from 30-120 seconds to 2-5 seconds.

**Building a sysimage (manual process):**

```julia
using PackageCompiler
julia_path = "src/esfex/julia"
create_sysimage(
    [:JuMP, :HiGHS, :Graphs, :LinearAlgebra];
    sysimage_path="esfex_sysimage.so",
    project=julia_path,
    precompile_execution_file="src/esfex/julia/src/precompile_workload.jl"
)
```

**Using a sysimage:**

Set the `PYTHON_JULIACALL_SYSIMAGE` environment variable before importing any ESFEX modules:

```bash
export PYTHON_JULIACALL_SYSIMAGE="/path/to/esfex_sysimage.so"
python -m esfex.cli run --config myconfig.yaml
```

`precompile_esfex()` provides a simpler but slower alternative using Julia's built-in precompilation system without creating a sysimage.

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `JULIA_NUM_THREADS` | Number of Julia threads | `4` (set by `initialize_julia`) |
| `PYTHON_JULIACALL_THREADS` | Alternative thread setting (juliacall-specific) | Not set |
| `JULIA_PROJECT` | Julia project path | Auto-configured to `src/esfex/julia/` |
| `PYTHON_JULIACALL_SYSIMAGE` | Path to pre-built sysimage for faster startup | Not set |
| `JULIA_DEPOT_PATH` | Julia package depot location | System default |

---

## Troubleshooting

### Common Errors

**`ImportError: juliacall is not installed`**

Install juliacall:
```bash
pip install juliacall
```

**`RuntimeError: Julia Project.toml not found`**

The Julia project directory is missing. Ensure the package is installed correctly and `src/esfex/julia/Project.toml` exists.

**`LoadError: ... package not found`**

Julia dependencies are not installed. Usually auto-resolved by `Pkg.instantiate()`, but if it fails:
```bash
cd src/esfex/julia
julia --project=. -e 'using Pkg; Pkg.instantiate()'
```

**`invalid redefinition of constant ESFEX`**

Harmless. Occurs when the Julia session already has the ESFEX module loaded (e.g., during interactive development). The module handles this automatically by reusing the existing module.

**Slow first startup (2-5 minutes)**

Normal behavior. Julia compiles all code on first use. To speed up subsequent runs:
1. Keep the Julia session alive (use the ESFEX Studio which maintains state).
2. Build a sysimage (see above).
3. Run `precompile_esfex()` once after installation.

**`Manifest.toml` corruption**

Delete `src/esfex/julia/Manifest.toml` and reinitialize. The initialization code attempts this automatically when `Pkg.instantiate()` fails.
