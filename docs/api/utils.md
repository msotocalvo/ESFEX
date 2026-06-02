# Utilities

Modules: `esfex.utils`, `esfex.utils.helpers`, `esfex.utils.temporal`

## Module: esfex.utils.helpers

### BoundaryConditions

Dataclass that stores the state carried between rolling horizon windows.

```python
@dataclass
class BoundaryConditions:
    battery_soc: Dict[int, Dict[int, float]]     # {bat_idx: {node: soc_value}}
    generator_status: Dict[int, Dict[int, int]]   # {gen_idx: {node: 0_or_1}}
    ev_soc: Dict[int, float]                      # {node: soc_value}
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `battery_soc` | `dict[int, dict[int, float]]` | Final SOC (MWh) of each battery at each node from the previous window. Used as initial SOC for the next window. |
| `generator_status` | `dict[int, dict[int, int]]` | Final on/off status (0 or 1) of each generator at each node. Only relevant in unit commitment mode. |
| `ev_soc` | `dict[int, float]` | Final EV fleet SOC per node. |

**Methods:**

| Method | Description |
|--------|-------------|
| `to_dict()` | Serialize to a plain dictionary for JSON/HDF5 storage. |
| `from_dict(data)` | Class method to deserialize from a dictionary. |

**Usage:**

```python
from esfex.utils.helpers import BoundaryConditions

# Create initial boundary conditions
bc = BoundaryConditions(
    battery_soc={0: {0: 50.0, 1: 30.0}},  # Battery 0: 50 MWh at node 0, 30 at node 1
    generator_status={0: {0: 1}, 1: {0: 1}},  # All generators on
    ev_soc={0: 100.0}  # EV fleet at node 0: 100 MWh
)

# Serialize for storage
data = bc.to_dict()

# Restore from storage
bc_restored = BoundaryConditions.from_dict(data)
```

### Initialization Functions

#### initialize_battery_soc

```python
def initialize_battery_soc(
    batteries: List[dict],
    num_nodes: int,
) -> Dict[int, Dict[int, float]]
```

Create initial battery SOC dictionary from configuration. Each battery initializes to its configured `soc_initial` value per node (default 0.5 = 50% of capacity).

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `batteries` | `list[dict]` | List of battery configuration dicts, each with a `soc_initial` list of per-node values. |
| `num_nodes` | `int` | Number of nodes in the network. |

**Returns:** `{bat_idx: {node: soc}}` dictionary.

#### initialize_generator_status

```python
def initialize_generator_status(
    generators: List[dict],
    num_nodes: int,
) -> Dict[int, Dict[int, int]]
```

Initialize generator on/off status. Generators with `rated_power > 0` at a node start ON (1), others start OFF (0).

**Returns:** `{gen_idx: {node: status}}` dictionary.

#### initialize_ev_soc

```python
def initialize_ev_soc(
    num_nodes: int,
    ev_initial_soc: Optional[List[float]] = None,
) -> Dict[int, float]
```

Initialize EV fleet SOC per node. Defaults to 0.5 (50%) if not specified.

**Returns:** `{node: soc}` dictionary.

### Extraction Functions

#### extract_boundary_conditions

```python
def extract_boundary_conditions(
    solution: dict,
    num_batteries: int,
    num_generators: int,
    num_nodes: int,
    default_battery_soc: Optional[List[dict]] = None,
    default_ev_soc: Optional[List[float]] = None,
) -> BoundaryConditions
```

Extract boundary conditions from a window solution for the next rolling horizon window. Takes final timestep values of battery SOC, generator status, and EV SOC from the solution dictionary.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `solution` | `dict` | Solution dictionary from `PowerSystemAdapter.get_solution_values()`. Expected keys: `bat_soc`, `gen_status`, `EV_soc`. |
| `num_batteries` | `int` | Number of batteries in the system. |
| `num_generators` | `int` | Number of generators. |
| `num_nodes` | `int` | Number of nodes. |
| `default_battery_soc` | `list[dict]` or `None` | Fallback SOC values if solution data is missing. |
| `default_ev_soc` | `list[float]` or `None` | Fallback EV SOC values. |

**Returns:** `BoundaryConditions` for the next window.

**Example:**

```python
from esfex.utils.helpers import (
    initialize_battery_soc,
    initialize_generator_status,
    initialize_ev_soc,
    extract_boundary_conditions,
    BoundaryConditions,
)

# Initialize for first window
bc = BoundaryConditions(
    battery_soc=initialize_battery_soc(batteries, num_nodes),
    generator_status=initialize_generator_status(generators, num_nodes),
    ev_soc=initialize_ev_soc(num_nodes),
)

# After solving a window:
bc = extract_boundary_conditions(
    solution=adapter.get_solution_values(),
    num_batteries=len(batteries),
    num_generators=len(generators),
    num_nodes=num_nodes,
)
# bc.battery_soc now contains end-of-window SOC values
```

#### extract_inertia_limit

```python
def extract_inertia_limit(
    inertia_limit: dict,
    start_hour: int,
    window_hours: int,
) -> Dict[int, float]
```

Extract inertia limit values for a specific rolling horizon window. Maps absolute-hour keys to window-relative timestamps.

**Returns:** `{t: limit}` dictionary for each timestep in the window (0-indexed within window).

#### extract_sectoral_demand

```python
def extract_sectoral_demand(
    sectoral_demand: Optional[Dict[str, np.ndarray]],
    start_hour: int,
    end_hour: int,
) -> Optional[Dict[str, np.ndarray]]
```

Slice sectoral demand arrays to the current window.

**Returns:** Sliced `{sector_name: np.ndarray(window_hours, nodes)}` or `None`.

#### extract_ev_profiles

```python
def extract_ev_profiles(
    ev_profiles: dict,
    ev_charging: Any,
    v2g_availability: Any,
    start_hour: int,
    end_hour: int,
) -> dict
```

Extract and slice EV charging and V2G profiles for the current window. Creates a deep copy of `ev_profiles` to avoid modifying the original. Updates `charging_profile` in `standard_charging` and `availability_profile` in `V2G` sub-dicts.

**Returns:** Modified EV profiles dict with windowed data.

### Metric Calculation Functions

#### calculate_renewable_penetration

```python
def calculate_renewable_penetration(
    gen_output: np.ndarray,
    generators: List[dict],
) -> Tuple[float, float, float]
```

Calculate renewable penetration metrics from generation output.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `gen_output` | `np.ndarray` | Generation output array with shape `[gen_idx, node, hour]`. |
| `generators` | `list[dict]` | Generator configurations. Each dict must have a `"type"` field (`"Renewable"` or other). |

**Returns:** Tuple of `(total_generation_MWh, renewable_generation_MWh, penetration_ratio)`.

**Example:**

```python
from esfex.utils.helpers import calculate_renewable_penetration

total, renewable, ratio = calculate_renewable_penetration(gen_output, generators)
print(f"RE penetration: {ratio:.1%}")  # e.g., "RE penetration: 72.3%"
```

#### calculate_co2_emissions

```python
def calculate_co2_emissions(
    gen_output: np.ndarray,
    generators: List[dict],
    fuel_co2: Dict[str, float],
) -> float
```

Calculate total CO2 emissions from generation output. Only non-renewable generators contribute, based on fuel type.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `gen_output` | `np.ndarray` | Generation output array `[gen_idx, node, hour]`. |
| `generators` | `list[dict]` | Generator configs with `"type"` and `"fuel"` fields. |
| `fuel_co2` | `dict[str, float]` | CO2 emission factors in tonnes/MWh. E.g., `{"Diesel": 0.267, "Natural Gas": 0.181}`. |

**Returns:** Total CO2 emissions in tonnes.

### Adjustment Functions

#### adjust_investment_limits

```python
def adjust_investment_limits(
    unit_data: dict,
    year: int,
    base_year: int,
    growth_rate: float = 0.5,
) -> None
```

Adjust investment limits for renewable and storage units based on year progression. Modifies `unit_data` in place.


Growth formula: `invest_max *= (1 + growth_rate) ^ (year - base_year)`.

Only applies to units with `type` in `("Renewable", "Storage")`.

#### adjust_transmission_parameters

```python
def adjust_transmission_parameters(
    nodes: dict,
    year: int,
    base_year: int,
    cost_reduction_rate: float = 0.03,
    capacity_growth_rate: float = 0.5,
) -> None
```

Adjust transmission investment costs and capacity limits based on year progression. Modifies `nodes` in place.


- Investment costs decrease: `cost *= (1 - cost_reduction_rate) ^ years_diff`.
- Capacity limits increase: `max *= (1 + capacity_growth_rate) ^ years_diff`.

---

## Module: esfex.utils.temporal

### Constants

```python
HOURS_STD_YEAR: int = 8760  # Standard (non-leap) year hours
```

Use `hours_for_year(year)` for year-specific calculations that account for leap years.

### aggregate_to_resolution

```python
def aggregate_to_resolution(
    data: Union[np.ndarray, pd.DataFrame],
    target_hours: int,
) -> Union[np.ndarray, pd.DataFrame]
```

Aggregate time series to a coarser resolution using **MEAN**. Appropriate for availability profiles (capacity factors).

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `data` | `np.ndarray` or `pd.DataFrame` | Hourly time series with shape `(timesteps,)` or `(timesteps, nodes, ...)`. |
| `target_hours` | `int` | Target resolution in hours. Must be a positive integer. |

**Returns:** Aggregated data with `len(data) // target_hours` rows. Same type as input.

**Raises:** `TypeError` if `target_hours` is not an integer; `ValueError` if `target_hours < 1`.

**Resolution examples:**

| Input | target_hours | Output | Description |
|-------|-------------|--------|-------------|
| 8760 timesteps | 1 | 8760 | Unchanged |
| 8760 timesteps | 3 | 2920 | 3-hourly |
| 8760 timesteps | 6 | 1460 | 6-hourly |
| 8760 timesteps | 24 | 365 | Daily |

If the input length is not divisible by `target_hours`, excess timesteps at the end are truncated silently.

**Example:**

```python
from esfex.utils.temporal import aggregate_to_resolution

hourly_avail = np.random.rand(8760, 4)  # 1 year, 4 nodes
sixhourly = aggregate_to_resolution(hourly_avail, target_hours=6)
print(sixhourly.shape)  # (1460, 4)
```

### aggregate_demand_to_resolution

```python
def aggregate_demand_to_resolution(
    data: Union[np.ndarray, pd.DataFrame],
    target_hours: int,
) -> Union[np.ndarray, pd.DataFrame]
```

Aggregate demand time series using **MAX** (not mean). Preserves peak demand within each aggregated period to avoid underestimating capacity requirements.

Same parameters and return type as `aggregate_to_resolution`.

Emits a `UserWarning` if the input length is not divisible by `target_hours`.

**Example:**

```python
from esfex.utils.temporal import aggregate_demand_to_resolution

hourly_demand = np.array([100, 150, 120, 180, 140, 110])
demand_6h = aggregate_demand_to_resolution(hourly_demand, target_hours=6)
print(demand_6h)  # [180.] -- MAX of the 6 values
```

### Why Different Aggregation Methods?

| Data Type | Method | Rationale |
|-----------|--------|-----------|
| Availability profiles | MEAN | Average capacity factor correctly represents energy available over the period. |
| Demand | MAX | System must meet peak demand within each period; averaging would undersize capacity. |
| Generation output | SUM | Total energy produced is additive. |
| Battery SOC | LAST | Final state carries forward to next period. |

### validate_hourly_data

```python
def validate_hourly_data(
    data: np.ndarray,
    expected_hours: int = 8760,
    data_name: str = "data",
) -> bool
```

Validate that input data has the expected number of timesteps.

**Raises:** `ValueError` if `data.shape[0] != expected_hours`.

**Returns:** `True` if validation passes.

### get_aggregated_timesteps

```python
def get_aggregated_timesteps(original_hours: int, target_hours: int) -> int
```

Calculate the number of timesteps after aggregation: `original_hours // target_hours`.

### get_hours_per_year

```python
def get_hours_per_year(leap_year: bool = False) -> int
```

Get hours in a year: 8760 (standard) or 8784 (leap year).

### hours_for_year

```python
def hours_for_year(year: int) -> int
```

Get hours for a specific calendar year. Uses `calendar.isleap()` for accuracy.

```python
from esfex.utils.temporal import hours_for_year

print(hours_for_year(2024))  # 8784 (leap year)
print(hours_for_year(2025))  # 8760
```

### calculate_rolling_horizon_windows

```python
def calculate_rolling_horizon_windows(
    total_hours: int,
    window_hours: int,
    overlap_hours: int,
) -> list[tuple[int, int]]
```

Calculate `(start_hour, end_hour)` pairs for rolling horizon dispatch.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `total_hours` | `int` | Total number of hours to cover (e.g., 8760 for one year). |
| `window_hours` | `int` | Hours per optimization window (e.g., 48). |
| `overlap_hours` | `int` | Hours of overlap between consecutive windows (e.g., 6). |

**Returns:** List of `(start, end)` tuples (0-indexed, end exclusive).

**Raises:** `ValueError` if `overlap_hours >= window_hours`.

Effective step between windows is `window_hours - overlap_hours`. Overlap ensures smooth transitions and boundary condition continuity. The overlap region of each window is discarded in favor of the next window's solution.

**Window structure:**

```
Window 1: |========= kept (42h) =========|overlap(6h)|
Window 2:                          |overlap|========= kept (42h) =========|overlap|
Window 3:                                                          |overlap|======= kept ======|
```

**Example:**

```python
from esfex.utils.temporal import calculate_rolling_horizon_windows

windows = calculate_rolling_horizon_windows(
    total_hours=8760,
    window_hours=48,
    overlap_hours=6,
)
print(f"Total windows: {len(windows)}")  # 209
print(windows[:3])  # [(0, 48), (42, 90), (84, 132)]
```

---

## Exported Symbols

```python
from esfex.utils import (
    # Helpers
    BoundaryConditions,
    adjust_investment_limits,
    adjust_transmission_parameters,
    calculate_co2_emissions,
    calculate_renewable_penetration,
    extract_boundary_conditions,
    extract_ev_profiles,
    extract_inertia_limit,
    extract_sectoral_demand,
    initialize_battery_soc,
    initialize_ev_soc,
    initialize_generator_status,
    # Temporal
    aggregate_demand_to_resolution,
    aggregate_to_resolution,
    calculate_rolling_horizon_windows,
    get_aggregated_timesteps,
    get_hours_per_year,
    validate_hourly_data,
)
```
