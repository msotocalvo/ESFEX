# Rooftop Solar Model
Module: `esfex.models.solar_rooftop`

## Overview

Rooftop solar differs from utility-scale solar in several ways [**[54]**](../reference/bibliography.md#ref54):
- It is distributed behind the meter, modeled as negative demand or fixed generation rather than a dispatchable generator.
- Adoption follows a logistic (S-curve) trajectory [**[53]**](../reference/bibliography.md#ref53) influenced by urbanization, policy, and economics.
- Weather variability (clouds, daily patterns) is modeled stochastically per node.
- Capacity potential is limited by building stock and rooftop suitability [**[55]**](../reference/bibliography.md#ref55).

---

## Functions

### generate_rooftop_solar_profiles

```python
def generate_rooftop_solar_profiles(
    num_nodes: int,
    hours: int = 24,
    base_year: int = 2024,
    target_year: int = 2050,
    adoption_scenario: str = "medium",
    weather_variability: str = "normal",
    seed: Optional[int] = None,
    config: Optional[dict] = None,
) -> Tuple[np.ndarray, np.ndarray, List[float]]
```

Generate stochastic rooftop solar availability and adoption profiles.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `num_nodes` | `int` | required | Number of network nodes. |
| `hours` | `int` | `24` | Number of hours to simulate. For multi-day simulations, the daily solar pattern repeats with stochastic weather variations. |
| `base_year` | `int` | `2024` | Base year for adoption calculation. |
| `target_year` | `int` | `2050` | Target year for adoption projections. |
| `adoption_scenario` | `str` | `"medium"` | Adoption scenario: `"low"`, `"medium"`, or `"high"`. Affects adoption growth rate and maximum penetration. |
| `weather_variability` | `str` | `"normal"` | Weather variability level: `"low"`, `"normal"`, or `"high"`. Controls day-to-day and node-to-node output variance. |
| `seed` | `int` or `None` | `None` | Random seed for reproducibility. |
| `config` | `dict` or `None` | `None` | Optional configuration dictionary for custom parameters. |

**Returns:** A 3-tuple:

| Index | Type | Shape | Description |
|-------|------|-------|-------------|
| 0 | `np.ndarray` | `(hours, nodes)` | Availability matrix, values in [0, 1]. Represents the fraction of installed capacity that is producing at each hour. |
| 1 | `np.ndarray` | `(nodes,)` | Adoption factors per node (0-1). Represents the fraction of maximum potential that is currently installed. |
| 2 | `list[float]` | `[nodes]` | Maximum installable capacity per node in MW. |

**Solar Profile Generation (Step by Step):**

1. **Base solar profile**: A bell-shaped curve from hour 6 to 18, computed as `sin(pi * (hour - 6) / 12)`. This repeats daily for multi-day simulations.

2. **Performance ratio**: Applied as a multiplicative factor (default 0.75) to account for system losses (wiring, inverter, soiling, etc.).

3. **Weather variability**: Per-day random weather factors drawn from `N(1.0, variance)`, clipped to [0.2, 1.8]. The variance depends on the `weather_variability` setting:

   | Setting | Weather Variance | Node Variance |
   |---------|-----------------|---------------|
   | `"low"` | 0.05 | 0.10 |
   | `"normal"` | 0.15 | 0.20 |
   | `"high"` | 0.25 | 0.30 |

4. **Cloud patterns**: 30% probability of cloud events per day. Each cloud event has a random start hour (6-16), duration (1-3 hours), and intensity (0.3-0.7 reduction factor).

5. **Node-specific variability**: Each node gets a random factor from `N(1.0, node_variance)`, clipped to [0.6, 1.4], plus small hourly noise.

6. **Final clipping**: All values clipped to [0, 1].

**Adoption Calculation:**

Adoption follows an S-curve per node:

```
adoption = max_adoption / (1 + exp(-growth_rate * (target_year - mid_point)))
```

Where:
- `max_adoption` depends on the scenario and urbanization:

  | Scenario | Max Adoption (base) |
  |----------|-------------------|
  | `"low"` | 0.30 |
  | `"medium"` | 0.50 |
  | `"high"` | 0.70 |

- `growth_rate` depends on the scenario:

  | Scenario | Growth Rate |
  |----------|------------|
  | `"low"` | 0.05 |
  | `"medium"` | 0.08 |
  | `"high"` | 0.12 |

- `mid_point` varies randomly per node around the midpoint of the planning horizon.
- Urbanization factor (Beta(2,2) distribution) modulates both max adoption and growth rate.

**Maximum Potential Calculation:**

If `systems_per_node` and `avg_system_size` are provided in `config`:
```
max_potential_MW = systems_per_node * avg_system_size_kW / 1000
```

Otherwise, estimated from a Gamma distribution scaled by urbanization.

**Configuration Dictionary Fields:**

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `performance_ratio` | `float` | `0.75` | System performance ratio. |
| `adoption_rates` | `dict` | `{"low": 0.05, "medium": 0.08, "high": 0.12}` | Growth rates by scenario. |
| `initial_adoption` | `list[float]` | `[0.05] * num_nodes` | Initial adoption fraction per node. |
| `max_adoption` | `dict` | `{"low": 0.30, "medium": 0.50, "high": 0.70}` | Max adoption by scenario. |
| `systems_per_node` | `list[int]` | Auto-estimated | Number of potential rooftop systems per node. |
| `avg_system_size` | `list[float]` | Auto-estimated | Average system size in kW per node. |

**Example:**

```python
from esfex.models.solar_rooftop import generate_rooftop_solar_profiles

availability, adoption, max_potential = generate_rooftop_solar_profiles(
    num_nodes=4,
    hours=8760,
    base_year=2025,
    target_year=2040,
    adoption_scenario="high",
    weather_variability="normal",
    seed=42,
)

# Installed capacity at each node
installed_mw = [adoption[n] * max_potential[n] for n in range(4)]
print(f"Total installed: {sum(installed_mw):.1f} MW")
print(f"Avg availability: {availability.mean():.3f}")
```

### integrate_rooftop_solar

```python
def integrate_rooftop_solar(
    units_config: Dict[str, dict],
    num_nodes: int,
    year: int,
    base_year: int,
    availability_matrix: np.ndarray,
    adoption_factors: np.ndarray,
    max_potential: List[float],
    co2_reduction: float = 0.7,
    cost_reduction_rate: float = 0.08,
    min_capacity_threshold: float = 1.0,
    config: Optional[dict] = None,
) -> Optional[dict]
```

Integrate rooftop solar as a virtual generator in the optimization model. Modifies `units_config` in place, adding a `"Rooftop_Solar"` unit.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `units_config` | `dict` | required | Unit configurations dictionary (modified in place). |
| `num_nodes` | `int` | required | Number of nodes. |
| `year` | `int` | required | Current modeling year. |
| `base_year` | `int` | required | Base year for projections. |
| `availability_matrix` | `np.ndarray` | required | From `generate_rooftop_solar_profiles()`. |
| `adoption_factors` | `np.ndarray` | required | From `generate_rooftop_solar_profiles()`. |
| `max_potential` | `list[float]` | required | From `generate_rooftop_solar_profiles()`. |
| `co2_reduction` | `float` | `0.7` | CO2 reduction factor vs conventional mix. |
| `cost_reduction_rate` | `float` | `0.08` | Annual learning curve cost reduction (8%/year). |
| `min_capacity_threshold` | `float` | `1.0` | Minimum total capacity (MW) to add the unit. |

**Returns:** The rooftop solar unit configuration dict, or `None` if below threshold.

**Capacity calculation:**

```python
# S-curve progress factor for the current year
progress_factor = min(1.0, years_diff / (target_year - base_year))
s_curve_factor = 1 / (1 + exp(-10 * (progress_factor - 0.5)))

# Current adoption (fraction of max potential)
current_adoption = adoption_factors * s_curve_factor

# Installed capacity per node
installed_capacity = max_potential * current_adoption * degradation_factor
```

The rooftop unit is added with:
- Type: `"Renewable"`, Fuel: `"Sun"`
- `rated_power`: installed capacity per node
- `invest_max_power`: remaining potential per node
- `invest_cost`: base cost reduced by learning curve
- `Availability`: the stochastic availability matrix

### calculate_rooftop_potential

```python
def calculate_rooftop_potential(
    population: List[float],
    dwelling_density: float = 0.35,
    avg_roof_area: float = 50.0,
    suitable_fraction: float = 0.3,
    panel_efficiency: float = 0.20,
    solar_irradiance: float = 1000.0,
) -> List[float]
```

Calculate rooftop solar potential from population demographics.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `population` | `list[float]` | required | Population per node. |
| `dwelling_density` | `float` | `0.35` | Dwellings per capita. |
| `avg_roof_area` | `float` | `50.0` | Average roof area in m^2. |
| `suitable_fraction` | `float` | `0.3` | Fraction of roof suitable for solar (orientation, shading). |
| `panel_efficiency` | `float` | `0.20` | Solar panel conversion efficiency (20%). |
| `solar_irradiance` | `float` | `1000.0` | Peak solar irradiance in W/m^2 (standard test conditions). |

**Returns:** List of maximum potential in MW per node.

**Formula:**

```
num_dwellings = population * dwelling_density
total_suitable_area = num_dwellings * avg_roof_area * suitable_fraction
peak_power_kW = total_suitable_area * panel_efficiency * solar_irradiance / 1000
max_potential_MW = peak_power_kW / 1000
```

**Example:**

```python
from esfex.models.solar_rooftop import calculate_rooftop_potential

populations = [50000, 30000, 20000, 10000]
potential = calculate_rooftop_potential(populations)
# potential ~ [52.5, 31.5, 21.0, 10.5] MW
```

---

## Integration with Capacity Expansion

Rooftop solar integration in the main simulation loop:

1. At simulation start, `generate_rooftop_solar_profiles()` creates availability and adoption data.
2. Each year, `integrate_rooftop_solar()` computes the installed capacity and adds a virtual generator to `units_config`.
3. The `PowerSystemAdapter` receives the rooftop generation profile via `kwargs['rooftop_generation']` and passes it to the Julia model.
4. In the Julia model, rooftop generation is treated as must-take renewable generation (subject to curtailment limits).

---

## YAML Configuration

```yaml
systems:
  island:
    rooftop_solar:
      enabled: true
      adoption_scenario: "medium"
      weather_variability: "normal"
      performance_ratio: 0.75
      cost_per_kw: 1200
      cost_reduction_rate: 0.08
      degradation_rate: 0.005
      o_and_m_cost: 20
      target_year: 2050
      systems_per_node: [5000, 3000, 2000, 1000]
      avg_system_size: [5.0, 5.0, 5.0, 5.0]  # kW
      initial_adoption: [0.05, 0.03, 0.02, 0.01]
```
