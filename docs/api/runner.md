# Runner

Module: `esfex.runner`

## Orchestrator

Coordinates multi-year capacity expansion and operational optimization.

```python
class Orchestrator:
    def __init__(
        self,
        config: ESFEXConfig,
        output_dir: Optional[Path] = None,
        config_path: Optional[Path] = None,
    ) -> None
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `config` | `ESFEXConfig` | (required) | Validated ESFEX configuration object |
| `output_dir` | `Optional[Path]` | `"./results"` | Directory for HDF5 output files and logs |
| `config_path` | `Optional[Path]` | `None` | Path to the original YAML file (for relative path resolution of demand files, availability profiles, etc.) |

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `config` | `ESFEXConfig` | The validated configuration |
| `output_dir` | `Path` | Output directory (created if needed) |
| `primary_system` | `SystemConfig` | The first system in the meta-network |
| `system_name` | `str` | Name of the primary system |
| `state` | `Optional[SimulationState]` | Current simulation state (set during `run()`) |
| `results` | `list[YearResults]` | Accumulated results across years |

**Example:**

```python
from esfex import load_config
from esfex.runner import Orchestrator

config = load_config("isla_juventud.yaml")
orch = Orchestrator(
    config,
    output_dir="./results",
    config_path=Path("isla_juventud.yaml"),
)
```

### run

```python
def run(
    self,
    years: Optional[int] = None,
    start_year: int = 2025,
) -> list[YearResults]
```

Execute the full simulation: Master Problem (capacity expansion) followed by year-by-year operational dispatch.


**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `years` | `Optional[int]` | `25` | Number of years to simulate |
| `start_year` | `int` | `2025` | First simulation year |

**Returns:** `list[YearResults]` -- One result object per simulated year.

**Raises:** Returns an empty list if the Master Problem fails.

**Workflow:**

1. Load and initialize plugins (`PluginManager.load_all()`)
2. **`pre_simulation`** plugin hook
3. Load demand data for all years (`_load_demand()`)
4. Generate EV charging profiles with S-curve growth (`_generate_ev_demand()`)
5. **`post_demand_loaded`** plugin hook (plugins may modify demand arrays)
6. Create sectoral demand distribution (`_create_sectoral_demand()`)
7. Preload availability profiles into cache (`_preload_availability_profiles()`)
8. Expand development zones if configured (`expand_config_with_zones()`)
9. Generate rooftop solar profiles if configured
10. Initialize HDF5 output file
11. **Step 1: Master Problem** (development mode only)
    - **`pre_master_problem`** plugin hook
    - Solve capacity expansion for all years simultaneously
    - **`post_master_problem`** plugin hook
12. **Step 2: Operational Dispatch** (year by year)
    - For each year:
        - Apply investment decisions from Master Problem
        - Build cumulative unit configuration (`_build_config_from_cumulative()`)
        - Rebuild generator/battery name lists for HDF5 alignment
        - **`pre_year`** plugin hook
        - Run rolling horizon dispatch (`_run_operational_dispatch()`)
        - Compute derived metrics (LCOE, VALLCOE, capacity factor)
        - Export year to HDF5
        - **`post_year`** plugin hook (receives open HDF5 handle)
13. Finalize HDF5 file
14. **`post_simulation`** plugin hook
15. `PluginManager.teardown_all()`

**Example:**

```python
results = orchestrator.run(years=25, start_year=2025)

for yr in results:
    print(f"Year {yr.year}: ${yr.objective:,.0f}")
    print(f"  RE penetration: {yr.re_penetration:.1%}")
    print(f"  Load shedding: {yr.load_shed:.1f} MWh")
    print(f"  Emissions: {yr.emissions:.0f} tCO2")
```

### Plugin Hooks

The Orchestrator dispatches hooks to all loaded plugins at key points in the pipeline. Plugins are discovered from `~/.esfex/plugins/` and loaded via the `PluginManager`. Each hook call is wrapped in `try/except` so a broken plugin is logged but never crashes the core simulation.

| Hook | Timing | Arguments |
|------|--------|-----------|
| `pre_simulation` | Before any computation | `config`, `output_dir` |
| `post_demand_loaded` | After demand loading | `base_demand`, `ev_demand`, `total_demand`, `config` |
| `pre_master_problem` | Before Master Problem solve | `config`, `years` |
| `post_master_problem` | After Master Problem solve | `investments`, `retirements`, `config` |
| `pre_year` | Before each year's dispatch | `year`, `year_idx`, `units_config`, `config` |
| `post_year` | After each year's dispatch | `year`, `year_result`, `hdf5_handle` |
| `post_simulation` | After all years complete | `results`, `config` |

See `esfex.plugins.protocol.ESFEXPlugin` for the full hook API.

### Internal Methods

These methods are called internally during `run()` but are documented for subclass extension:

| Method | Description |
|--------|-------------|
| `_load_demand(years, start_year)` | Load base demand for all years from file |
| `_generate_ev_demand(num_nodes, total_hours, ...)` | Generate S-curve EV profiles |
| `_create_sectoral_demand(total_demand)` | Distribute demand into sectors |
| `_preload_availability_profiles(num_nodes)` | Cache all availability files at startup |
| `_solve_master_problem(years_range, demand, ...)` | Solve strategic capacity expansion |
| `_run_operational_dispatch(year, demand, ...)` | Rolling horizon dispatch for one year |
| `_solve_window(demand, hours, ...)` | Solve a single dispatch window |
| `_build_config_from_cumulative(units, caps)` | Build unit config with investments applied |
| `_rebuild_unit_names(units_config)` | Rebuild name lists to match Julia ordering |
| `_compute_derived_metrics(year_result, ...)` | Compute LCOE, VALLCOE, capacity factor |
| `_calculate_initial_re_penetration()` | Auto-calculate initial RE penetration from existing capacity |
| `_apply_transfer_investments(investments)` | Apply transmission line investment decisions |
| `_extract_year_demand(total_demand, y_idx, hours)` | Slice year-specific demand from multi-year array |

---

## SimulationState

Tracks simulation state across years and rolling horizon windows.

```python
@dataclass
class SimulationState:
    year: int
    base_year: int
    units_config: dict[str, Any]
    boundary_conditions: dict[str, Any] = field(default_factory=dict)
    cumulative_investments: dict[str, Any] = field(default_factory=dict)
    cumulative_retirements: dict[str, Any] = field(default_factory=dict)
    primary_energy_capacities: dict[str, Any] = field(
        default_factory=lambda: {"storage": {}, "transport": {}}
    )
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `year` | `int` | Current simulation year |
| `base_year` | `int` | First year of the simulation |
| `units_config` | `dict[str, Any]` | Current unit configuration (includes investment adjustments) |
| `boundary_conditions` | `dict[str, Any]` | Rolling horizon boundary state (battery SOC, generator status) |
| `cumulative_investments` | `dict[str, Any]` | Accumulated investment decisions up to current year |
| `cumulative_retirements` | `dict[str, Any]` | Accumulated retirement decisions up to current year |
| `primary_energy_capacities` | `dict[str, Any]` | Primary energy storage and transport capacity state |

---

## YearResults

Complete results for one simulation year, including all optimization variables, derived metrics, and scalar summaries.

```python
@dataclass
class YearResults:
    year: int
    objective: float
    solve_time: float
    feasible: bool
    ...
```

### Core Fields

| Field | Type | Shape | Description |
|-------|------|-------|-------------|
| `year` | `int` | scalar | Simulation year |
| `objective` | `float` | scalar | Total system cost ($) |
| `solve_time` | `float` | scalar | Total solve time (seconds) |
| `feasible` | `bool` | scalar | Whether all windows were feasible |

### Generation Arrays

| Field | Type | Shape | Description |
|-------|------|-------|-------------|
| `gen_output` | `Optional[np.ndarray]` | `(gen, node, hour)` | Generator output (MW) |
| `gen_status` | `Optional[np.ndarray]` | `(gen, node, hour)` | Generator on/off status (UC mode) |
| `gen_startup` | `Optional[np.ndarray]` | `(gen, node, hour)` | Startup events |
| `gen_shutdown` | `Optional[np.ndarray]` | `(gen, node, hour)` | Shutdown events |
| `curtailment` | `Optional[np.ndarray]` | `(gen, node, hour)` | RE curtailment (MW) |

### Storage Arrays

| Field | Type | Shape | Description |
|-------|------|-------|-------------|
| `bat_charge` | `Optional[np.ndarray]` | `(bat, node, hour)` | Charging power (MW) |
| `bat_discharge` | `Optional[np.ndarray]` | `(bat, node, hour)` | Discharging power (MW) |
| `bat_soc` | `Optional[np.ndarray]` | `(bat, node, hour)` | State of charge (MWh) |
| `bat_spillage` | `Optional[np.ndarray]` | `(bat, node, hour)` | Battery spillage (MW) |

### Reserve and Reliability Arrays

| Field | Type | Shape | Description |
|-------|------|-------|-------------|
| `reserve_static` | `Optional[np.ndarray]` | `(node, hour)` | Static reserve (MW) |
| `reserve_dynamic` | `Optional[np.ndarray]` | `(node, hour)` | Dynamic reserve (MW) |
| `loss_of_reserve_static` | `Optional[np.ndarray]` | `(node, hour)` | Static reserve deficit (MW) |
| `loss_of_reserve_dynamic` | `Optional[np.ndarray]` | `(node, hour)` | Dynamic reserve deficit (MW) |
| `load_shed_array` | `Optional[np.ndarray]` | `(node, hour)` | Load shedding (MW) |
| `loss_of_inertia` | `Optional[np.ndarray]` | `(hour,)` | Inertia deficit (MW*s) |

### Network and Prices

| Field | Type | Shape | Description |
|-------|------|-------|-------------|
| `power_flow` | `Optional[dict]` | `{(from,to): array}` | Line power flows (MW) |
| `voltage_angle` | `Optional[np.ndarray]` | `(node, hour)` | Bus voltage angles (rad) |
| `transfer_investment` | `Optional[dict]` | `{(from,to): float}` | Transmission investment (MW) |
| `prices` | `Optional[np.ndarray]` | `(node, hour)` | Nodal prices ($/MWh, dual of power balance) |
| `demand` | `Optional[np.ndarray]` | `(hour, node)` | Demand used in optimization (MW) |

### EV Variables

| Field | Type | Shape | Description |
|-------|------|-------|-------------|
| `ev_charging` | `Optional[np.ndarray]` | `(node, hour)` | EV charging demand (MW) |
| `ev_v2g` | `Optional[np.ndarray]` | `(node, hour)` | V2G discharge (MW) |
| `ev_soc` | `Optional[np.ndarray]` | `(node, hour)` | EV fleet SOC (MWh) |
| `ev_loss` | `Optional[np.ndarray]` | `(node, hour)` | Unmet EV demand (MW) |

### Investment and Retirement

| Field | Type | Description |
|-------|------|-------------|
| `investments` | `dict[str, float]` | Year-specific investment decisions from Master Problem |
| `retirements` | `dict[str, float]` | Year-specific retirement decisions |
| `gen_investment_array` | `Optional[np.ndarray]` | Generator investment `(gen, node)` in MW |
| `bat_investment_power` | `Optional[np.ndarray]` | Battery power investment `(bat, node)` in MW |
| `bat_investment_capacity` | `Optional[np.ndarray]` | Battery energy investment `(bat, node)` in MWh |

### Derived Metrics

| Field | Type | Shape | Description |
|-------|------|-------|-------------|
| `capacity_factor` | `Optional[np.ndarray]` | `(gen, node, hour)` | Generator capacity factors |
| `lcoe` | `Optional[np.ndarray]` | `(gen, node, hour)` | Levelized cost of energy ($/MWh) |
| `vallcoe` | `Optional[np.ndarray]` | `(gen, node, hour)` | Value-adjusted LCOE ($/MWh) |
| `bat_capacity_factor` | `Optional[np.ndarray]` | `(bat, node, hour)` | Battery capacity factors |
| `bat_lcoe` | `Optional[np.ndarray]` | `(bat, node, hour)` | Battery LCOE ($/MWh) |
| `fuel_for_power` | `Optional[dict]` | `{gen_idx: array}` | Fuel consumption per generator |
| `technology_selling_prices` | `Optional[dict]` | `{tech: dict}` | Technology-level selling prices |

### System-Level Scalars

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `emissions` | `float` | `0.0` | Total CO2 emissions (tonnes) |
| `re_penetration` | `float` | `0.0` | Renewable energy penetration fraction |
| `load_shed` | `float` | `0.0` | Total unserved energy (MWh) |
| `total_generation` | `float` | `0.0` | Total energy generated (MWh) |
| `total_demand` | `float` | `0.0` | Total demand (MWh) |
| `master_re_target` | `float` | `0.0` | RE target from Master Problem for this year |

### Cost Breakdown

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cost_breakdown` | `Optional[dict]` | `None` | Per-component decomposition of the annual system cost (fuel, O&M, CO2, investment, penalties, etc.). Contains 27 cost component keys plus `total`. See [HDF5 Output Schema — cost_breakdown](../reference/hdf5-output-schema.md#cost_breakdown) for the full list of fields. `None` when cost decomposition is not available. |

### Reservoir Hydroelectric

| Field | Type | Shape | Description |
|-------|------|-------|-------------|
| `reservoir_level` | `Optional[np.ndarray]` | `(gen, node, hour)` | Reservoir storage level (MWh) |
| `reservoir_spillage` | `Optional[np.ndarray]` | `(gen, node, hour)` | Reservoir spillage (MW) |
| `reservoir_pump` | `Optional[np.ndarray]` | `(gen, node, hour)` | Pump-storage pumping (MW) |
| `reservoir_invest_capacity` | `Optional[np.ndarray]` | `(gen, node)` | Reservoir capacity investment (MWh) |
