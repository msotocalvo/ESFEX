# System Architecture

ESFEX is organized in three layers: Python orchestration, a Python–Julia bridge for data conversion, and Julia optimization.


## High-Level Overview

```
+-------------------------------------------------------------+
|                      Python Layer                            |
|  config/schema.py   runner.py   io/demand.py   models/ev.py |
|  plugins/manager.py  sensitivity/engine.py  visualization/  |
+------------------------------+------------------------------+
                               |
                    Python <-> Julia Bridge
                               |
              +----------------+------------------+
              |        bridge/adapters.py          |
              |  bridge/converters.py              |
              |  bridge/julia_setup.py (juliacall) |
              +----------------+------------------+
                               |
+------------------------------+------------------------------+
|                       Julia Layer                            |
|  power_system.jl   master_problem.jl   transmission_dc.jl   |
|  primary_energy.jl   electrolyzer.jl   mga.jl   types.jl    |
+-------------------------------------------------------------+
                               |
                          JuMP + Solver
                     (HiGHS / Gurobi / CPLEX)
```

---


## Python Layer

| Module | Role |
|--------|------|
| `config/schema.py` | Pydantic v2 models for all configuration |
| `config/loader.py` | YAML file loading with validation |
| `config/solver.py` | Solver detection and configuration |
| `runner.py` | Orchestrator -- the main simulation loop |
| `io/demand.py` | Demand data loading from Excel/CSV |
| `io/exporter.py` | HDF5 results export to CSV, Excel, JSON |
| `models/ev.py` | EV fleet S-curve adoption and charging profiles |
| `models/solar_rooftop.py` | Rooftop solar adoption and generation |
| `zones.py` | Development zone preprocessor (virtual nodes) |
| `plugins/` | Plugin discovery, loading, and hook dispatch |
| `sensitivity/` | Sobol global sensitivity analysis [**[11]**](../reference/bibliography.md#ref11) |
| `visualization/` | PySide6 Studio with Leaflet.js map |

### Runner (`runner.py`)

The runner is the central orchestrator:

1. Loads and validates the YAML configuration via Pydantic
2. Loads demand data for all simulation years
3. Generates EV charging profiles (S-curve growth model)
4. Preloads renewable availability profiles into an in-memory cache
5. Expands development zones into virtual nodes
6. Solves the multi-year master problem (capacity expansion)
7. Iterates year-by-year through operational dispatch (rolling horizon)
8. Computes derived metrics (LCOE, VALLCOE, capacity factors)
9. Exports all results to HDF5

The runner maintains a `SimulationState` dataclass that tracks cumulative investments, retirements, boundary conditions, and primary energy capacities across years.

---


## Bridge Layer

The bridge (`bridge/`) converts between Python data structures (NumPy arrays, Pydantic models) and Julia data structures (JuMP models, Julia structs).


### Adapter Classes

| Adapter | Julia Module | Purpose |
|---------|-------------|---------|
| `PowerSystemAdapter` | `power_system.jl` | Operational dispatch and unit commitment |
| `MasterProblemAdapter` | `master_problem.jl` | Multi-year capacity expansion planning |
| `PrimaryEnergyAdapter` | `primary_energy.jl` | Fuel supply chain optimization |
| `ElectrolyzerAdapter` | `electrolyzer.jl` | Power-to-hydrogen modeling |
| `MGAAdapter` | `mga.jl` | Near-optimal alternative generation (SPORES) |

Each adapter follows the same pattern:

1. Receives Python configuration objects (Pydantic models, NumPy arrays)
2. Calls converter functions to translate data into Julia-compatible types
3. Invokes the corresponding Julia function via `juliacall`
4. Receives Julia result structs and converts them back to Python (NumPy arrays, dicts)

### How Python and Julia Interact (juliacall)

`juliacall` embeds a Julia runtime inside the Python process. The initialization sequence:

```
Python process starts
    |
    v
julia_setup.initialize_julia(threads=4)
    |
    v
juliacall imports Julia Main module
    |
    v
Pkg.activate("src/esfex/julia/")  -- activates the Julia project
    |
    v
include("src/esfex/julia/src/ESFEX.jl")  -- loads the ESFEX module
    |
    v
using .ESFEX  -- makes all exported functions available
    |
    v
Adapters call jl.seval("ESFEX.function_name(args...)")
```

Key characteristics:

- **Single process**: Julia runs in the same process as Python (no subprocess or IPC overhead)
- **Shared memory**: NumPy arrays are converted to Julia arrays using `juliacall` type conversions
- **Lazy initialization**: Julia is only started when the first adapter is used
- **Caching**: Once initialized, the Julia runtime persists for the session
- **Thread control**: Julia thread count is set via `JULIA_NUM_THREADS` before initialization

### Data Type Conversions (`converters.py`)

| Python Type | Julia Type | Function |
|------------|-----------|----------|
| `np.ndarray` (1D) | `Vector{Float64}` | `py_to_julia_vector()` |
| `np.ndarray` (2D) | `Matrix{Float64}` | `py_to_julia_matrix()` |
| `list[int]` | `Vector{Int64}` | `py_to_julia_int_vector()` |
| `GeneratorConfig` | `GeneratorConfig` (Julia struct) | `convert_generator_config()` |
| `BatteryConfig` | `BatteryConfig` (Julia struct) | `convert_battery_config()` |
| `TechnologyConfig` | `TechnologyConfig` (Julia struct) | `convert_technology_config()` |
| `dict` (network) | `NetworkConfig` (Julia struct) | `convert_network_config()` |

---


## Julia Layer

The Julia code (`julia/src/`) implements the optimization models using JuMP [**[20]**](../reference/bibliography.md#ref20):

| File | Lines | Purpose |
|------|-------|---------|
| `ESFEX.jl` | ~100 | Module definition, exports, includes |
| `types.jl` | ~700 | Input/output data structures (structs) |
| `power_system.jl` | ~2,500 | Operational dispatch and unit commitment |
| `master_problem.jl` | ~3,600 | Multi-year capacity expansion |
| `transmission_dc.jl` | ~760 | DC power flow (KCL, KVL, cycle constraints) |
| `transmission_ac.jl` | ~500 | AC power flow verification (Newton-Raphson post-DC) |
| `primary_energy.jl` | ~1,580 | Fuel supply chain optimization |
| `electrolyzer.jl` | ~290 | Power-to-hydrogen conversion |
| `mga.jl` | ~350 | MGA/SPORES near-optimal alternative generation |

HiGHS [**[21]**](../reference/bibliography.md#ref21) is the default solver. Gurobi, CPLEX, SCIP, Xpress, CBC, and GLPK can be substituted via configuration.

### Optimization Models

| Model | File | Description |
|-------|------|-------------|
| Master Problem | `master_problem.jl` | Multi-year LP for investment/retirement decisions across all years simultaneously |
| Operational Dispatch | `power_system.jl` | Hourly dispatch via rolling horizon; LP (economic dispatch) or MIP (unit commitment) |
| DC Power Flow | `transmission_dc.jl` | KCL/KVL constraints [**[1]**](../reference/bibliography.md#ref1), cycle-based formulation, N-1 contingency analysis [**[34]**](../reference/bibliography.md#ref34) |
| AC Power Flow | `transmission_ac.jl` | Post-DC Newton-Raphson verification of voltages, reactive power, and losses [**[5]**](../reference/bibliography.md#ref5) |
| Primary Energy | `primary_energy.jl` | Fuel supply chains with storage, transport, and import constraints |
| Electrolyzer | `electrolyzer.jl` | Power-to-hydrogen conversion with capacity investment and efficiency modeling |
| MGA/SPORES | `mga.jl` | Near-optimal alternative generation [**[7]**](../reference/bibliography.md#ref7), [**[8]**](../reference/bibliography.md#ref8) using SPORES frequency-based scoring |

---


## Simulation Flow

```
 1. Load Config (YAML)
    |
    v
 2. Load Demand (Excel/CSV, all years)
    |
    v
 3. Generate EV Profiles (S-curve growth)
    |
    v
 4. Generate Rooftop Solar Profiles
    |
    v
 5. Preload Availability Profiles (cache)
    |
    v
 6. Expand Development Zones (virtual nodes)
    |
    v
 7. Solve Master Problem (all years, strategic)
    |   --> investments, retirements, transmission expansion
    v
 8. For each YEAR (y = 1..N):
    |
    |   8a. Apply cumulative investments to config
    |       (create virtual generators/batteries)
    |
    |   8b. Rebuild unit names for HDF5 export
    |
    |   8c. Solve Primary Energy (fuel supply chain)
    |
    |   8d. Rolling Horizon Dispatch:
    |       For each WINDOW (w = 1..W):
    |         - Extract window demand slice
    |         - Get availability profiles from cache
    |         - Solve PowerSystem (dispatch/UC)
    |         - Stitch results (discard overlap)
    |
    |   8e. Compute Derived Metrics
    |       (LCOE, VALLCOE, capacity factors, CO2)
    |
    |   8f. Write year results to HDF5
    |
    v
 9. Finalize HDF5, Plugin post_simulation hooks
```

### Rolling Horizon Detail

The operational dispatch uses a rolling horizon to keep solve times manageable. Both window size (`rolling_horizon_hours`) and overlap (`overlap_hours`) are user-configurable:

```
Year (8760 hours)
|<-------- Window 1 -------->|
                     |<-- overlap -->|
                          |<-------- Window 2 -------->|
                                              |<-- overlap -->|
                                                   |<--- Window 3 --->|
                                                   ...
```

- Window size and overlap are set via `rolling_horizon_hours` and `overlap_hours` in the configuration
- Results from the overlap region of the previous window provide boundary conditions (battery SOC, generator status) for the next window
- Only the non-overlapping portion of each window's results is kept

## File Organization

```
src/esfex/
+-- __init__.py          # Package entry: load_config, ESFEXConfig
+-- cli.py               # CLI commands (typer)
+-- runner.py             # Orchestrator
+-- zones.py              # Development zone preprocessor
+-- config/
|   +-- schema.py         # Pydantic models
|   +-- loader.py         # YAML loading
|   +-- solver.py         # Solver management
+-- bridge/
|   +-- adapters.py       # Julia model wrappers (5 adapters)
|   +-- converters.py     # Data type conversion (Python <-> Julia)
|   +-- julia_setup.py    # Julia runtime initialization (juliacall)
+-- io/
|   +-- demand.py         # Demand data loading
|   +-- exporter.py       # Results export (HDF5, CSV, Excel, JSON)
+-- models/
|   +-- ev.py             # EV fleet modeling (S-curve adoption)
|   +-- solar_rooftop.py  # Rooftop solar adoption
+-- plugins/
|   +-- protocol.py       # Plugin base class (ESFEXPlugin)
|   +-- manager.py        # Plugin discovery, loading, lifecycle
|   +-- availability_generator/  # Built-in plugin: solar/wind profiles
+-- sensitivity/
|   +-- engine.py         # Sobol global sensitivity analysis
+-- utils/
|   +-- temporal.py       # Time resolution utilities
+-- julia/
|   +-- Project.toml      # Julia project dependencies
|   +-- Manifest.toml     # Julia dependency lockfile
|   +-- src/
|       +-- ESFEX.jl         # Module definition
|       +-- types.jl          # Data structures
|       +-- power_system.jl   # Operational dispatch
|       +-- master_problem.jl # Capacity expansion
|       +-- transmission_dc.jl # DC power flow
|       +-- transmission_ac.jl # AC power flow
|       +-- primary_energy.jl  # Fuel supply chain
|       +-- electrolyzer.jl    # Power-to-hydrogen
|       +-- mga.jl             # MGA/SPORES alternatives
+-- visualization/        # Studio (PySide6 + Leaflet.js)
    +-- app.py            # Application entry point
    +-- main_window.py    # Main window layout and orchestration
    +-- map_widget.py     # Leaflet.js map (QWebEngineView + QWebChannel)
    +-- i18n.py           # Internationalization
    +-- data/
    |   +-- gui_model.py  # GUI data model (GuiSystemState)
    |   +-- serializer.py # YAML export from GUI model
    +-- panels/           # Property forms, element tree, results
```

---


## Plugin System

ESFEX includes a directory-based plugin system. Plugins extend ESFEX without modifying its source code.

### Plugin Discovery

Plugins are discovered from three locations, in order:

1. `~/.esfex/plugins/` -- user-level plugins
2. `<project_dir>/.esfex/plugins/` -- project-level plugins
3. Directories listed in the `$ESFEX_PLUGIN_PATH` environment variable

Each plugin is a directory containing:

- `plugin.json` -- metadata (name, version, description, dependencies)
- `__init__.py` -- factory function `create_plugin(context) -> ESFEXPlugin`

### Plugin Hooks

Plugins can hook into the simulation lifecycle at these points:

| Hook | When |
|------|------|
| `setup()` | After plugin instantiation |
| `on_config_loaded()` | After configuration is validated |
| `pre_simulation()` | Before simulation starts |
| `post_demand_loaded()` | After demand data is loaded (can modify demand) |
| `pre_master_problem()` | Before capacity expansion solve |
| `post_master_problem()` | After capacity expansion solve |
| `pre_year()` | Before each year's dispatch |
| `post_year()` | After each year's results (can write to HDF5) |
| `post_simulation()` | After all years complete |
| `teardown()` | On shutdown |

### Julia Extensions

Plugins can provide Julia modules via `get_julia_modules()`. These are `include()`-d at runtime as overlays -- the core `.jl` files are never modified. This allows plugins to add custom constraints, variables, or objective terms to the optimization models.

### Plugin Security

- Plugin names are sanitized (alphanumeric, underscore, hyphen only)
- ZIP installation validates against Zip Slip (CWE-22)
- Git clone disables hooks to prevent pre-checkout RCE
- Git URLs restricted to `https://` and `git://` schemes
- SHA-256 hash of plugin contents is logged on load

---


## GUI Architecture

The Studio (`visualization/`) is a PySide6 desktop application with an embedded Leaflet.js map.

### Components

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Main window | PySide6 (`QMainWindow`) | Layout, menus, toolbar |
| Map widget | Leaflet.js via `QWebEngineView` | Geographic node/line placement |
| Element tree | PySide6 (`QTreeWidget`) | Hierarchical element browser |
| Property panels | PySide6 (`QWidget` forms) | Element configuration forms |
| Bridge | `QWebChannel` | Python-JS bidirectional communication |

### Data Model

The GUI uses its own data model (`GuiSystemState`) with instance-based elements (one object per generator/battery/line, not per-node arrays). The serializer converts this GUI model into the YAML configuration format expected by the optimizer.

### Multi-System Support

The GUI supports editing multiple power systems simultaneously. Each system has its own `GuiSystemState`, tree root node, and map layers. The user can switch between systems via the element tree.

### Map Interaction

- **Nodes**: Placed and dragged on the map with position snapping
- **Lines**: Drawn as polyline traces with waypoints (start trace, add waypoints, finish)
- **Equipment**: Generators, batteries, transformers, and fuel entries snap to nodes via a magnetic registry
- **Drag propagation**: Moving a node automatically updates all connected equipment and line endpoints

See [GUI Editor](../gui/overview.md) for details.
