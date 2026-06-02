# EV Model
Module: `esfex.models.ev`

## S-Curve Growth Model

All EV profile functions use a logistic (S-curve) growth model [**[53]**](../reference/bibliography.md#ref53) to project fleet size over time. The V2G (vehicle-to-grid) framework follows the fundamentals described in [**[51]**](../reference/bibliography.md#ref51) and [**[52]**](../reference/bibliography.md#ref52):

```
N(t) = N_max / (1 + exp(-k * (t - t_mid)))
```

Where:
- `N(t)` is the fleet growth factor at year `t`.
- `N_max` is the maximum growth multiplier (`max_adoption` parameter).
- `k` is the logistic growth rate (`growth_rate` parameter).
- `t_mid` is the midpoint year where adoption reaches 50% of maximum.

The midpoint is calculated as:

```
t_mid = base_year + (target_year - base_year) * mid_point_fraction
```

Each EV category can have its own `max_adoption`, `growth_rate`, and `mid_point_fraction` parameters, allowing different adoption curves for private cars, buses, trucks, etc.

**Example growth curve:**

For `base_year=2025`, `target_year=2050`, `max_adoption=30`, `growth_rate=0.12`, `mid_point_fraction=0.5`:
- 2025: growth_factor ~1.0 (initial fleet)
- 2037: growth_factor ~15.0 (midpoint)
- 2050: growth_factor ~29.5 (approaching maximum)

---

## Functions

### generate_ev_profiles

```python
def generate_ev_profiles(
    num_nodes: int,
    num_hours: int,
    ev_categories: Dict[str, dict],
    ev_quantity: Dict[str, List[float]],
    base_patterns: Dict[str, List[float]],
    base_year: int = 2025,
    target_year: int = 2050,
    max_adoption: float = 30.0,
    growth_rate: float = 0.12,
) -> pd.DataFrame
```

Generate EV charging demand profiles with S-curve fleet growth.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `num_nodes` | `int` | required | Number of network nodes. |
| `num_hours` | `int` | required | Total simulation hours (e.g., `25 * 8760` for 25 years). |
| `ev_categories` | `dict` | required | EV category configurations. Keys are category names (e.g., `"private_car"`, `"bus"`, `"truck"`). Values are dicts with fields described below. |
| `ev_quantity` | `dict` | required | Initial fleet count per category per node (base year). Keys are category names, values are lists of vehicle counts per node. |
| `base_patterns` | `dict` | required | 24-hour charging availability templates per category. Keys are category names, values are lists of 24 floats in [0, 1] representing the fraction of fleet charging at each hour of day. |
| `base_year` | `int` | `2025` | Fleet baseline year. |
| `target_year` | `int` | `2050` | Current simulation year (used for S-curve calculation). |
| `max_adoption` | `float` | `30.0` | Default maximum fleet growth multiplier (can be overridden per category). |
| `growth_rate` | `float` | `0.12` | Default logistic growth rate (can be overridden per category). |

**`ev_categories` dict fields:**

| Key | Type | Description |
|-----|------|-------------|
| `charging_power` | `float` | Charging power per vehicle in kW. |
| `v2g_participation` | `float` | Fraction of fleet participating in V2G (0-1). |
| `v2g_power` | `float` | V2G discharge power per vehicle in kW. |
| `max_adoption` | `float` | Category-specific max growth multiplier (optional, overrides default). |
| `growth_rate` | `float` | Category-specific growth rate (optional, overrides default). |
| `mid_point_fraction` | `float` | Fraction of total years at which adoption reaches 50% (optional, default 0.5). |

**Returns:** `pd.DataFrame` with:
- Index: hour indices (0 to `num_hours - 1`).
- Columns: `"Node_{n}_{category}"` for each node-category combination.
- Values: charging demand in MW.

**Charging demand calculation:**

```
demand_MW = pattern[hour_of_day] * num_vehicles * charging_power_kW / 1000
```

Where `num_vehicles = initial_quantity * growth_factor(year)`.

Small Gaussian noise (std=0.02) is added to the base pattern for realism.

**Example:**

```python
from esfex.models.ev import generate_ev_profiles

ev_categories = {
    "private_car": {
        "charging_power": 7.4,     # kW
        "v2g_participation": 0.3,
        "v2g_power": 5.0,         # kW
        "max_adoption": 30.0,
        "growth_rate": 0.12,
    },
    "bus": {
        "charging_power": 150.0,
        "v2g_participation": 0.0,
        "v2g_power": 0.0,
        "max_adoption": 10.0,
        "growth_rate": 0.08,
    },
}

ev_quantity = {
    "private_car": [5000, 3000, 2000, 1000],  # vehicles per node
    "bus": [50, 30, 20, 10],
}

# 24h charging pattern: home charging peaks evening, low during work hours
base_patterns = {
    "private_car": [0.3, 0.2, 0.1, 0.1, 0.1, 0.1, 0.2, 0.3,
                    0.1, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.1,
                    0.2, 0.4, 0.6, 0.8, 0.9, 0.7, 0.5, 0.4],
    "bus": [0.8, 0.8, 0.8, 0.8, 0.5, 0.1, 0.0, 0.0,
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 0.1, 0.3, 0.5, 0.7, 0.8, 0.8],
}

profiles = generate_ev_profiles(
    num_nodes=4, num_hours=8760,
    ev_categories=ev_categories,
    ev_quantity=ev_quantity,
    base_patterns=base_patterns,
    base_year=2025, target_year=2035,
)
print(profiles.columns.tolist())
# ['Node_1_private_car', 'Node_1_bus', 'Node_2_private_car', ...]
print(f"Peak charging: {profiles.max().max():.1f} MW")
```

### generate_v2g_availability

```python
def generate_v2g_availability(
    num_nodes: int,
    num_hours: int,
    ev_categories: Dict[str, dict],
    ev_quantity: Dict[str, List[float]],
    base_patterns: Dict[str, List[float]],
    base_year: int = 2025,
    target_year: int = 2050,
    max_adoption: float = 30.0,
    growth_rate: float = 0.12,
) -> pd.DataFrame
```

Generate V2G (Vehicle-to-Grid) availability profiles with S-curve fleet growth.

Parameters are identical to `generate_ev_profiles`. V2G availability represents the fraction of the fleet available to discharge back to the grid, scaled by the `v2g_participation` rate and `v2g_power` per vehicle.

**V2G availability calculation:**

```
v2g_MW = pattern[hour_of_day] * v2g_participation * num_vehicles * v2g_power_kW / 1000
```

**Returns:** `pd.DataFrame` with V2G availability in MW. Same column naming as `generate_ev_profiles`.

### aggregate_ev_profiles

```python
def aggregate_ev_profiles(
    profiles: pd.DataFrame,
    num_nodes: int,
) -> np.ndarray
```

Aggregate category-level EV profiles to node-level demand, summing across all categories at each node.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `profiles` | `pd.DataFrame` | EV profiles from `generate_ev_profiles()` with columns `"Node_{n}_{category}"`. |
| `num_nodes` | `int` | Number of nodes. |

**Returns:** NumPy array of shape `(hours, num_nodes)` in MW. Each entry is the sum of all EV category charging demands at that node and hour.

**Example:**

```python
from esfex.models.ev import generate_ev_profiles, aggregate_ev_profiles

profiles = generate_ev_profiles(num_nodes=4, num_hours=8760, ...)
aggregated = aggregate_ev_profiles(profiles, num_nodes=4)
# aggregated.shape == (8760, 4)
```

### generate_electricity_prices

```python
def generate_electricity_prices(num_hours: int = 24) -> np.ndarray
```

Generate synthetic electricity prices with morning and evening peak patterns.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `num_hours` | `int` | `24` | Number of hours to generate. |

**Returns:** NumPy array of electricity prices in $/MWh. Range approximately 50-200 $/MWh with Gaussian noise (std=5).

The price curve uses two Gaussian peaks:
- Morning peak at hour 9 (sigma=1.5)
- Evening peak at hour 20 (sigma=2.0)

### calculate_v2g_compensation

```python
def calculate_v2g_compensation(electricity_prices: np.ndarray) -> np.ndarray
```

Calculate V2G compensation rates as 85% of electricity prices.

**Returns:** V2G compensation rates in $/MWh.

### save_ev_profiles_hdf5

```python
def save_ev_profiles_hdf5(
    ev_charging: pd.DataFrame,
    v2g_availability: pd.DataFrame,
    filepath: Optional[str] = None,
) -> str
```

Save EV charging and V2G profiles to an HDF5 file for later reuse.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `ev_charging` | `pd.DataFrame` | EV charging profiles from `generate_ev_profiles()`. |
| `v2g_availability` | `pd.DataFrame` | V2G profiles from `generate_v2g_availability()`. |
| `filepath` | `str` or `None` | Output path. If `None`, creates a temp file with a UUID name. |

**Returns:** Path to the created HDF5 file.

**HDF5 structure:**

```
ev_profiles_{uuid}.h5
    charging/
        data      [hours x categories*nodes]
        index     [hours]
        columns   [category names]
    v2g/
        data      [hours x categories*nodes]
        index     [hours]
        columns   [category names]
```

### load_ev_profiles_hdf5

```python
def load_ev_profiles_hdf5(filepath: str) -> Tuple[pd.DataFrame, pd.DataFrame]
```

Load EV profiles from a previously saved HDF5 file.

**Returns:** Tuple of `(ev_charging, v2g_availability)` DataFrames.

---

## Integration with Optimization

EV demand integrates into the power system in two ways:

1. **Fixed demand**: Add aggregated EV profiles to base demand via `total_demand = base_demand + ev_demand`. Used when EV optimization is disabled.

2. **Optimizable V2G**: Pass EV configuration to the optimizer via `ev_config_data` in `PowerSystemAdapter`. The Julia model then creates EV charging/discharging decision variables with SOC tracking constraints. This allows V2G to provide grid services (peak shaving, frequency regulation).

When EV optimization is enabled, EV demand should NOT be added to `total_demand` to avoid double-counting.
