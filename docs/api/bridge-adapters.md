# Bridge Adapters

Module: `esfex.bridge.adapters`

## Data Flow Overview

```
Python config (Pydantic)
    --> adapter.__init__() stores configuration
    --> adapter._create_input() converts to Julia InputData struct
    --> Julia solver builds JuMP model, solves, extracts solution
    --> adapter.get_solution_values() converts Julia result to Python dict
```

For operational dispatch:

```
ESFEXConfig / SystemConfig
    --> PowerSystemAdapter._create_input()
        --> convert_generator_config() per generator
        --> convert_battery_config() per battery
        --> convert_network_config() for DC power flow topology
        --> ESFEX.PowerSystemInput(...) Julia struct
    --> ESFEX.create_power_system(input) --> (model, vars)  [JuMP [20]](../reference/bibliography.md#ref20)
    --> JuMP.optimize!(model)
    --> ESFEX.extract_solution(model, vars, input) --> PowerSystemResult
    --> convert_power_system_result() --> Python dict
```

For capacity expansion:

```
ESFEXConfig / SystemConfig + years + demand
    --> MasterProblemAdapter._create_input()
        --> convert generators, batteries, technologies, battery_technologies
        --> convert_network_config() for transmission
        --> ESFEX.MasterProblemInput(...) Julia struct
    --> ESFEX.create_master_problem(input) --> (model, vars, targets)
    --> JuMP.optimize!(model)
    --> ESFEX.extract_master_solution(...) --> MasterProblemResult
    --> Python dict with investment/retirement decisions per year
```

---

## PowerSystemAdapter

Wraps the operational dispatch model (`power_system.jl`). Solves short-horizon economic dispatch or unit commitment problems.

```python
class PowerSystemAdapter:
    def __init__(
        self,
        config: Union[ESFEXConfig, SystemConfig],
        demand: np.ndarray,              # (hours x nodes) in MW
        hours: int,
        num_nodes: int,
        year: int,
        base_year: int,
        mode: str = "development",
        availability_cache: Optional[Dict[str, np.ndarray]] = None,
        inflow_cache: Optional[Dict[str, np.ndarray]] = None,
        start_hour: int = 0,
        **kwargs
    ) -> None
```

**Key Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `config` | `ESFEXConfig` or `SystemConfig` | System configuration. If `ESFEXConfig`, temporal and solver settings are extracted automatically. |
| `demand` | `np.ndarray` | Demand array for the current window, shape `(hours, num_nodes)` in MW. |
| `hours` | `int` | Number of timesteps in this window. |
| `num_nodes` | `int` | Number of geographic nodes. |
| `year` | `int` | Current simulation year (used for age-based retirement). |
| `base_year` | `int` | Base year for investment age calculations. |
| `mode` | `str` | `"development"` (LP relaxation), `"economic_dispatch"` (LP, no investment), or `"unit_commitment"` (MIP with binary commitment variables). |
| `availability_cache` | `dict` | Pre-loaded availability profiles keyed by generator config key. Each value is a full-year NumPy array of shape `(8760, nodes)` with values in [0, 1]. Avoids repeated file I/O when solving multiple windows. |
| `inflow_cache` | `dict` | Pre-loaded reservoir inflow profiles keyed by generator config key. Same format as `availability_cache`. |
| `start_hour` | `int` | Start hour within the year for slicing availability and inflow profiles to the current window. |

**Keyword Arguments (via `**kwargs`):**

| Key | Type | Description |
|-----|------|-------------|
| `units_config` | `dict` | Updated unit capacities from MasterProblem investments. Keys are generator/battery config keys, values are dicts with `rated_power`, `capacity`, etc. Virtual generators/batteries created by `_build_config_from_cumulative()` are also included here. |
| `sectoral_demand` | `dict` | Sectoral demand arrays `{sector_name: np.ndarray(hours, nodes)}`. |
| `rooftop_generation` | `np.ndarray` | Rooftop solar generation profile `(hours, nodes)` in MW. |
| `re_penetration_target` | `float` | Year-specific RE penetration target override (0-1). |
| `ev_config_data` | `dict` | EV fleet configuration for V2G constraints. |
| `electricity_price` | `list[float]` | Time-varying electricity prices for V2G compensation. |
| `unit_npv` | `dict` | NPV values per (generator, bus) for lifecycle-aware dispatch. |
| `replacement_needed` | `dict` | Boolean flags per (generator, bus) indicating replacement needed. |

**Methods:**

### build_model

```python
def build_model(self, external_model=None) -> None
```

Build the JuMP optimization model. Calls `_create_input()` to convert Python config to a Julia `PowerSystemInput` struct, then calls `ESFEX.create_power_system(input)` to construct the JuMP model.

### _create_input

```python
def _create_input(self) -> Any  # Julia PowerSystemInput
```

Converts Python configuration into a Julia `PowerSystemInput` struct. Steps:

1. Iterates over `sys.generators` and converts each to a Julia `GeneratorConfig` via `convert_generator_config()`.
2. Resolves availability profiles from `availability_cache` (slicing to the current window using `start_hour`) or loads from file as fallback.
3. Applies `units_config` overrides to update `rated_power` for generators whose capacity was modified by MasterProblem investments.
4. Creates virtual generators for technology investments that do not exist in `sys.generators` (e.g., "Investment Solar PV").
5. Repeats the above for batteries and virtual batteries.
6. Applies geographic fuel transport cost adjustments based on generator-to-fuel-storage distance.
7. Builds network, temporal, penalty, and solver configuration Julia structs.
8. Constructs the final `ESFEX.PowerSystemInput(...)` with all fields.

### solve

```python
def solve(self) -> int
```

Solve the optimization model. Returns a PuLP-compatible status code:

| Return Value | Meaning |
|-------------|---------|
| `1` | Optimal solution found |
| `0` | Not solved |
| `-1` | Infeasible |
| `-2` | Unbounded |

### get_solution_values

```python
def get_solution_values(self) -> dict
```

Extract all variable values from the solved model. Calls `ESFEX.extract_solution()` on the Julia side and converts to a Python dictionary with NumPy arrays. Returns keys such as `gen_output`, `bat_charge`, `bat_discharge`, `bat_soc`, `curtailment`, `loss_of_load`, `transfer`, `gen_status`, `electricity_prices`, `nodal_electricity_prices`, etc.

When cost decomposition is available, the result dictionary also includes a `cost_breakdown` key — a dict with 27 cost component fields (fuel, O&M, CO2, investment, etc.) plus `total`. See [HDF5 Output Schema — cost_breakdown](../reference/hdf5-output-schema.md#cost_breakdown) for the full field list.

### get_objective_value

```python
def get_objective_value(self) -> float
```

Return the objective function value (total system cost for the window in $).

### write_lp

```python
def write_lp(self, filepath: str) -> None
```

Export the JuMP model to LP format for debugging. Requires `build_model()` to have been called first.

---

## MasterProblemAdapter

Wraps the capacity expansion model (`master_problem.jl`). Determines optimal investment and retirement decisions across a multi-year planning horizon.

```python
class MasterProblemAdapter:
    def __init__(
        self,
        config: Union[ESFEXConfig, SystemConfig],
        years: List[int],
        base_year: int,
        demand: np.ndarray,
        demand_growth: float = 0.02,
        discount_rate: float = 0.05,
        max_annual_investment: float = 1e9,
        target_re_penetration: float = 0.5,
        initial_re_penetration: float = 0.1,
        min_re_increment: float = 0.0,
        max_re_increment: float = 1.0,
        slack_penalty: float = 1e6,
        life_extension_cost_factor: float = 0.3,
        decommissioning_cost_factor: float = 0.1,
        temporal_resolution_hours: int = 24,
        representative_days_per_year: int = 12,
        min_day_separation: int = 7,
        use_tsam: bool = False,
        tsam_period_start_hours: Optional[List[List[int]]] = None,
        tsam_period_weights: Optional[List[List[float]]] = None,
        tsam_chronological_order: Optional[List[List[int]]] = None,
        tsam_inter_period_linking: bool = True,
        use_stochastic: bool = False,
        stochastic_scenarios: Optional[List[dict]] = None,
        config_path: Optional[str] = None,
        availability_cache: Optional[Dict[str, np.ndarray]] = None,
        **kwargs
    ) -> None
```

**Key Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `years` | `list[int]` | Planning horizon years, e.g. `[2025, 2026, ..., 2050]`. |
| `base_year` | `int` | Base year for NPV discounting. |
| `demand` | `np.ndarray` | Base demand array `(hours, nodes)`. Applied with `demand_growth` for each year. |
| `demand_growth` | `float` | Annual demand growth rate (e.g., 0.02 = 2%). |
| `discount_rate` | `float` | Discount rate for NPV calculations (e.g., 0.05 = 5%). |
| `max_annual_investment` | `float` | Maximum annual investment budget in $. |
| `target_re_penetration` | `float` | Target RE penetration ratio by the final year (0-1). |
| `initial_re_penetration` | `float` | Starting RE penetration ratio (auto-calculated from existing RE capacity). |
| `temporal_resolution_hours` | `int` | Hours per timestep for representative days (e.g., 24). |
| `representative_days_per_year` | `int` | Number of representative days per year for operational validation. |
| `use_tsam` | `bool` | Use Time Series Aggregation Method (TSAM) clustering instead of peak-day selection. |
| `tsam_period_start_hours` | `list[list[int]]` | Per-year start hours for TSAM periods (1-indexed). |
| `tsam_period_weights` | `list[list[float]]` | Per-year weights for TSAM periods. |
| `tsam_inter_period_linking` | `bool` | Enable inter-period SOC linking for battery continuity across representative periods. |
| `use_stochastic` | `bool` | Enable two-stage stochastic programming with scenario trees. |
| `config_path` | `str` | Path to config file for resolving relative availability file paths. |
| `availability_cache` | `dict` | Pre-loaded availability profiles for generators and technologies. |

**Methods:**

### build_model

```python
def build_model(self, use_representative_days: bool = True) -> None
```

Build the JuMP capacity expansion model. If `use_representative_days` is True, includes operational validation constraints using representative days (peak demand days or TSAM clusters).

### solve

```python
def solve(self) -> int
```

Optimize the master problem. Returns PuLP-compatible status code.

### get_solution_values

```python
def get_solution_values(self) -> dict
```

Extract investment and retirement decisions keyed by year. Returns a dictionary with:

- Per-year generator investment power (MW) per technology per node
- Per-year battery investment power and capacity (MW/MWh) per technology per node
- Per-year transmission investment capacity (MW) per line
- Cumulative capacities per year
- Retirement decisions
- Total NPV cost

### write_lp

```python
def write_lp(self, filepath: str) -> None
```

Export the master problem LP file for debugging or sensitivity analysis.

---

## TransmissionDCAdapter

Wraps DC power flow graph construction using Kirchhoff's voltage law formulation.

```python
class TransmissionDCAdapter:
    def __init__(
        self,
        num_nodes: int,
        nodes_config: NodeConfig,
        fuel_transport_distances: List[List[float]],
        base_impedance: float = 100.0,
        reactance_per_km: float = 0.4,
        voltage_level_kv: float = 220.0,
        enable_angle_limits: bool = True,
        max_angle_diff_deg: float = 30.0,
        transmission_lines_geo=None,
        transformers=None,
        acdc_converters=None,
        freq_converters=None,
        buses=None,
    ) -> None
```

**Properties:**

| Property | Type | Description |
|----------|------|-------------|
| `lines` | `list[tuple]` | List of `(from_bus, to_bus)` pairs (0-indexed). |
| `line_reactances` | `dict[tuple, float]` | Per-line reactance keyed by `(from, to)`. |
| `incidence_matrix` | `np.ndarray` | Bus-line incidence matrix. |
| `cycle_matrix` | `np.ndarray` | Line-cycle matrix for Kirchhoff's voltage law. |
| `independent_cycles` | `list[list[tuple]]` | Detected independent network cycles. |

---

## MGAAdapter

Wraps near-optimal alternative exploration (`mga.jl`). Two methods share this adapter, dispatched by `MGAConfig.method`:

- **`"mga"`** (default): wraps Julia's `run_mga_spores` — the classical Hop-Skip-Jump loop. Produces `num_alternatives` alternatives, each maximising a diversity objective weighted by the frequency score `1 − 2·freq`.
- **`"spores"`**: wraps Julia's `run_spores` — the per-objective sweep. Solves one alternative per entry in `MGAConfig.objectives`, with each entry mapped to its matching `Symbol` (see [§ SPORES](julia-api.md#sporesphase-2)).

Both paths return a uniform dictionary; each alternative carries an `objective` tag so downstream consumers (runner export, viewer) render them with one code path.

```python
class MGAAdapter:
    def __init__(
        self,
        master_adapter: MasterProblemAdapter,
        mga_config: MGAConfig
    ) -> None
```

**Key Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `master_adapter` | `MasterProblemAdapter` | A fully configured MasterProblemAdapter instance. Provides system data, demand, and Julia input construction. The adapter must NOT have been solved yet -- MGAAdapter handles the initial solve internally. |
| `mga_config` | `MGAConfig` | Settings from `config.master_problem.mga`. Fields: `enabled`, `method`, `objectives` (SPORES only), `num_alternatives` (MGA only), `slack_fraction`, `investment_threshold`. See [MGAConfig](config-schema.md#mgaconfig). |

**Methods:**

### run

```python
def run(self, use_representative_days: bool = True) -> dict
```

Execute the configured method (MGA or SPORES). The Julia input is built once via `master._create_input()` and reused; both Julia entry points construct their own JuMP model internally. Returns a dictionary with:

| Key | Type | Description |
|-----|------|-------------|
| `method` | `str` | `"mga"` or `"spores"` — the method that was actually run |
| `num_alternatives` | `int` | Total number of alternatives (including cost-optimal) |
| `slack_fraction` | `float` | Cost slack used |
| `optimal_cost` | `float` | Cost-optimal objective value ($) |
| `alternatives` | `list[dict]` | Per-alternative results, ordered with the cost-optimal seed at index 0 |

Each alternative dict contains:

| Key | Type | Description |
|-----|------|-------------|
| `alternative_id` | `int` | 0 = cost-optimal, 1..K = non-optimal alternatives |
| `is_optimal` | `bool` | `True` for alternative 0 |
| `cost` | `float` | Actual system cost for this alternative ($) |
| `diversity_objective` | `float` or `None` | Objective value at this alternative. `None` for the cost-optimal seed |
| `objective` | `str` | SPORES tag: `"cost_optimal"` for the seed, `"hsj_diversity"` for any MGA alt, or one of `"min_total_build"`, `"max_tech_equity"`, `"max_regional_equity"`, `"evolutionary_dist"` for SPORES alts |
| Investment data | various | Same structure as `MasterProblemAdapter.get_solution_values()` |

**Example — classical MGA:**

```python
from esfex.bridge.adapters import MasterProblemAdapter, MGAAdapter
from esfex.config.schema import MGAConfig

master = MasterProblemAdapter(config, years, base_year, demand, ...)
mga_cfg = MGAConfig(
    enabled=True, method="mga",
    num_alternatives=10, slack_fraction=0.05,
)
mga = MGAAdapter(master, mga_cfg)
result = mga.run(use_representative_days=True)

assert result["method"] == "mga"
for alt in result["alternatives"]:
    print(f"Alt {alt['alternative_id']}: cost={alt['cost']:.0f}, "
          f"objective={alt['objective']}, "
          f"diversity={alt['diversity_objective']}")
```

**Example — SPORES sweep:**

```python
from esfex.config.schema import MGAConfig, SporesObjective

mga_cfg = MGAConfig(
    enabled=True, method="spores",
    objectives=[
        SporesObjective.MIN_TOTAL_BUILD,
        SporesObjective.MAX_TECH_EQUITY,
        SporesObjective.MAX_REGIONAL_EQUITY,
        SporesObjective.EVOLUTIONARY_DIST,
    ],
    slack_fraction=0.05,
)
mga = MGAAdapter(master, mga_cfg)
result = mga.run(use_representative_days=True)

assert result["method"] == "spores"
assert result["num_alternatives"] == 1 + 4  # cost-optimal + 4 objectives
for alt in result["alternatives"]:
    print(f"Alt {alt['alternative_id']:>2}  "
          f"objective={alt['objective']:<22}  "
          f"cost=${alt['cost']/1e9:.2f}B")
```

Internally, `_run_spores` coerces `SporesObjective` enum members to their string `.value`s, marshals them into a Julia `Vector{Symbol}` via `seval("Symbol[…]")` (using `json.dumps` for the string literals — Python's `repr` would emit single quotes, which Julia parses as character literals), and forwards the vector to `ESFEX.run_spores`.

---

## units_config Parameter

Bridges strategic (MasterProblem) and operational (PowerSystem) optimization layers. After solving the master problem, the runner calls `_build_config_from_cumulative()` to compute cumulative installed capacities per year. These are stored in a `units_config` dictionary passed to `PowerSystemAdapter` via `kwargs['units_config']`.

**Structure:**

```python
units_config = {
    # Existing generator with updated capacity from investments
    "unit_3": {
        "rated_power": [0.0, 50.0, 0.0],  # MW per node
        "degradation_rate": [0.0, 0.0, 0.0],  # set to 0 (already applied in master)
        "initial_age": [0, 0, 0],  # set to 0 (age tracked in master)
    },
    # Virtual generator from technology investment
    "tech_solar_pv": {
        "name": "Investment Solar PV",
        "type": "Renewable",
        "fuel": "Sun",
        "rated_power": [0.0, 87.5, 0.0],
        "availability_file": "solar_availability.csv",
        "_type": "generator",
    },
    # Virtual battery from battery technology investment
    "tech_li_ion": {
        "name": "Investment Battery",
        "_type": "battery",
        "capacity": [0.0, 96.0, 0.0],  # MWh per node
        "MaxChargePower": [0.0, 24.0, 0.0],  # MW per node
        "MaxDischargePower": [0.0, 24.0, 0.0],
    },
}
```

When `PowerSystemAdapter._create_input()` encounters a key in `units_config`:
- If the key matches an existing generator in `sys.generators`, the `rated_power` is overridden.
- If the key is new (not in `sys.generators` or `sys.batteries`), a virtual generator or battery is created from the dict and added to the JuMP model.

---

## availability_cache Parameter

Dictionary mapping generator config keys to full-year NumPy arrays. Populated once at simulation startup by `runner._preload_availability_profiles()` and reused across all rolling horizon windows, avoiding thousands of redundant file loads.

**Structure:**

```python
availability_cache = {
    "unit_0": np.ndarray(shape=(8760, 4)),  # Solar PV availability, 4 nodes
    "unit_1": np.ndarray(shape=(8760, 4)),  # Wind availability
    "unit_2": np.ndarray(shape=(8760, 4)),  # Hydro availability
}
```

Inside `_create_input()`, the cache is sliced to the current window:

```python
# For hourly resolution:
availability = cached[start_hour : start_hour + hours]

# For coarser resolution (e.g., 6-hourly):
resampled = aggregate_to_resolution(cached, target_hours=resolution_hours)
start_idx = start_hour // resolution_hours
availability = resampled[start_idx : start_idx + hours]
```

---

## ElectrolyzerAdapter

Wraps the electrolyzer model (`electrolyzer.jl`). Handles hydrogen production optimization coupled with the power system.

---

## PrimaryEnergyAdapter

Wraps the primary energy model (`primary_energy.jl`). Optimizes fuel supply chains including storage, transport, and procurement.

---

## Helper Functions

### _compute_geographic_fuel_adjustments

```python
def _compute_geographic_fuel_adjustments(sys_config: SystemConfig) -> Dict[str, List[float]]
```

Computes fuel cost adjustments based on geographic distance between generators and their nearest fuel storage facilities using haversine distance and per-fuel transport cost rates. Returns a dict mapping generator keys to adjusted fuel cost arrays.

### _resolve_element_bus_mapping

```python
def _resolve_element_bus_mapping(sys_config: SystemConfig) -> tuple[dict, dict]
```

Resolve generator-to-bus and battery-to-bus mappings from `transmission_lines_geo` endpoint data. Returns `(gen_to_bus, bat_to_bus)` dicts mapping config keys to 0-based bus indices.
