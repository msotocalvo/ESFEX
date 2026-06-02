# Primary Energy

## Overview

The primary energy module models the fuel supply chain from import/production to consumption [**[49]**](../reference/bibliography.md#ref49). It operates at multiple temporal scales: hourly for electricity-sector fuel consumption and daily/weekly for fuel logistics (supply scheduling, transport, and storage).

The module captures:

- **Fuel supply** at import/production nodes with cost and availability limits
- **Fuel transport** between nodes with capacity, losses, and investment options
- **Fuel storage** with dynamic inventory management and safety stock
- **Non-electric fuel demand** for transportation, industrial, and residential sectors
- **Environmental emissions** from both electric and non-electric fuel combustion
- **Investment** in storage and transport infrastructure

---

## Scenario

An island imports diesel fuel by tanker ship to a port node and natural gas via a pipeline from a neighboring island. The system balances fuel import costs, storage, and transport against electricity generation needs, while also serving non-electric fuel demand from transportation and industrial sectors.

---

## Step 1: Enable Primary Energy

```yaml
enable_primary_energy: true
```

When this flag is `false` (the default), generators consume fuel at a flat cost per MWh without any supply chain constraints. When `true`, fuel must be explicitly sourced, transported, and stored.

---

## Step 2: Define Fuel Types

Each fuel type has physical properties that determine its emission profile and energy equivalence:

```yaml
systems:
  island:
    fuels:
      diesel:
        emission_factor: 0.267     # tCO2/MWh_thermal
        energy_content: 11.86      # MWh/tonne
        price: 800.0               # $/tonne (base procurement price)

      natural_gas:
        emission_factor: 0.202     # tCO2/MWh_thermal
        energy_content: 13.89      # MWh/tonne (LNG equivalent)
        price: 350.0               # $/tonne

      lng:
        emission_factor: 0.202     # tCO2/MWh_thermal (same as NG)
        energy_content: 13.89      # MWh/tonne
        price: 600.0               # $/tonne (higher due to liquefaction)

      hydrogen:
        emission_factor: 0.0       # Zero direct emissions (green H2)
        energy_content: 33.33      # MWh/tonne
        price: 3000.0              # $/tonne (current green H2 cost)
```

### Fuel Properties Reference

| Property | Units | Description |
|----------|-------|-------------|
| `emission_factor` | tCO2/MWh_thermal | CO2 emissions per unit of thermal energy. Set to 0 for zero-emission fuels. |
| `energy_content` | MWh/tonne | Energy density used to convert between mass and energy units. |
| `price` | $/tonne | Base procurement cost at the supply source. This is the minimum cost; actual landed cost may be higher due to transport. |

---

## Step 3: Configure Primary Energy Sources

Each source is tied to a specific fuel type and node:

```yaml
    primary_energy_sources:
      diesel_import:
        fuel: diesel
        node: 0                     # Port node where tankers deliver
        max_supply: 500.0           # tonnes/period (max delivery rate)
        import_cost: 850.0          # $/tonne (landed cost including shipping)
        storage_capacity: 10000.0   # tonnes (tank farm capacity)
        storage_min: 1000.0         # tonnes (safety stock minimum)
        storage_initial: 5000.0     # tonnes (starting inventory)

      gas_pipeline:
        fuel: natural_gas
        node: 0                     # Pipeline entry at same port node
        max_supply: 300.0           # tonnes/period
        import_cost: 400.0          # $/tonne (pipeline delivery cost)
        storage_capacity: 2000.0    # tonnes (pressurized storage)
        storage_min: 200.0          # tonnes (safety stock)
        storage_initial: 1000.0     # tonnes

      diesel_secondary:
        fuel: diesel
        node: 1                     # Secondary import at inland node
        max_supply: 100.0           # tonnes/period (smaller delivery)
        import_cost: 950.0          # $/tonne (higher cost due to logistics)
        storage_capacity: 3000.0    # tonnes
        storage_min: 300.0
        storage_initial: 1500.0
```

### Source Properties Reference

| Property | Units | Description |
|----------|-------|-------------|
| `fuel` | string | Fuel type name (must match a key in `fuels`) |
| `node` | integer | Node index where fuel enters the system |
| `max_supply` | tonnes/period | Maximum delivery rate per primary energy period |
| `import_cost` | $/tonne | Full landed cost (includes shipping, handling, duties) |
| `storage_capacity` | tonnes | Total storage tank capacity at this node |
| `storage_min` | tonnes | Safety stock that must always be maintained |
| `storage_initial` | tonnes | Starting inventory level at simulation start |

---

## Step 4: Configure Fuel Entry Points

Fuel entry points define which fuels are available at each node for power generation:

```yaml
    fuel_entry_points:
      - node: 0
        fuels: [diesel, natural_gas]
        max_import_rate: 500.0      # tonnes/period

      - node: 1
        fuels: [diesel]
        max_import_rate: 200.0

      - node: 2
        fuels: [diesel]
        max_import_rate: 150.0
```

---

## Step 5: Configure Fuel Transport Routes

Transport routes define how fuel moves from import nodes to consumption nodes:

```yaml
    transport_routes:
      - from_node: 0
        to_node: 1
        distance_km: 150.0
        fuel_params:
          diesel:
            capacity: 200.0         # tonnes/day (truck fleet capacity)
            transport_losses: 0.5   # % per 100 km (evaporation, spillage)
            transport_cost: 10.0    # $/tonne/km
          natural_gas:
            capacity: 100.0         # tonnes/day (pipeline capacity)
            transport_losses: 0.2   # % per 100 km (pipeline leakage)
            transport_cost: 5.0     # $/tonne/km

      - from_node: 1
        to_node: 2
        distance_km: 100.0
        fuel_params:
          diesel:
            capacity: 100.0
            transport_losses: 0.5
            transport_cost: 12.0    # Higher cost (worse roads)
```

### Transport Properties Reference

| Property | Units | Description |
|----------|-------|-------------|
| `from_node` | integer | Origin node index |
| `to_node` | integer | Destination node index |
| `distance_km` | km | Physical route distance (affects losses and cost) |
| `capacity` | tonnes/day | Maximum daily throughput per fuel type |
| `transport_losses` | %/100km | Fraction lost per 100 km (evaporation, leakage, spillage) |
| `transport_cost` | $/tonne/km | Operating cost per unit per kilometer |

The effective fuel received at the destination is:

```
received = sent * (1 - loss_rate * distance_km / 100)
```

For example, sending 100 tonnes of diesel over 150 km with 0.5% loss per 100 km:

```
received = 100 * (1 - 0.005 * 150 / 100) = 100 * 0.9925 = 99.25 tonnes
```

---

## Step 6: Configure Fuel Storage Infrastructure

```yaml
    primary_energy_infrastructure:
      diesel:
        storage_efficiency: 0.98        # Round-trip efficiency (accounts for handling losses)
        storage_expansion_limit: 2.0    # Max expansion = 2x base capacity
        storage_invest_cost: 50.0       # $/tonne storage investment cost
      natural_gas:
        storage_efficiency: 0.95        # Lower for pressurized gas
        storage_expansion_limit: 1.5
        storage_invest_cost: 200.0      # More expensive pressurized vessels
```

`storage_expansion_limit` prevents unbounded construction. A value of 2.0 means total capacity (existing + new) cannot exceed 3x the original.

---

## Step 7: Configure Non-Electric Fuel Demand

Non-electric demand captures fuel consumption outside the electricity sector — transportation, industry, cooking, and heating:

```yaml
    non_electric_demand:
      transport_diesel:
        fuel: diesel
        sector: transport
        demand_per_node: [50.0, 25.0, 15.0]  # tonnes/period per node
        criticality: 0.8                       # High priority (0-1 scale)

      industrial_gas:
        fuel: natural_gas
        sector: industrial
        demand_per_node: [30.0, 10.0, 0.0]    # Only at nodes 0 and 1
        criticality: 0.9                       # Very high priority

      residential_lpg:
        fuel: diesel                           # Proxy for LPG
        sector: residential
        demand_per_node: [10.0, 8.0, 5.0]
        criticality: 0.7
```

### Criticality

The `criticality` parameter (0 to 1) determines the priority of non-electric fuel demand:

- **1.0**: Absolutely critical — fuel shortfall triggers maximum penalty
- **0.8-0.9**: High priority — transportation, essential industry
- **0.5-0.7**: Medium priority — commercial, non-essential residential
- **0.0-0.3**: Low priority — can be curtailed with minimal penalty

When fuel supply is insufficient to meet both electric and non-electric demand, higher-criticality demand is served first.

---

## Complete YAML Example

```yaml
simulation_mode: development
date_start: "01/01/2025 00:00"
enable_primary_energy: true

temporal:
  resolution_hours: 1
  use_rolling_horizon: true
  rolling_horizon_hours: 48
  overlap_hours: 6
  primary_energy_resolution: 24     # Daily fuel periods (reduces variables)

solver:
  name: highs
  threads: 4
  time_limit: 3600

master_problem:
  representative_days_per_year: 5

meta_network:
  systems: [island]

systems:
  island:
    name: island
    demand_path: demand_25years.xlsx
    demand_scale: 1.0
    demand_growth: 0.02
    discount_rate: 0.05
    target_re_penetration: 0.60
    max_curtailment_ratio: 0.05
    MAX_ANNUAL_SYSTEM_COST: 500000000.0

    nodes:
      adjacency_matrix:
        - [0, 100, 75]
        - [100, 0, 50]
        - [75, 50, 0]
      coordinates:
        - [-82.38, 23.13]
        - [-81.95, 22.40]
        - [-80.45, 22.07]
      names: ["Port_City", "Inland_Hub", "Eastern_Town"]

    fuels:
      diesel:
        emission_factor: 0.267
        energy_content: 11.86
        price: 800.0
      natural_gas:
        emission_factor: 0.202
        energy_content: 13.89
        price: 350.0

    primary_energy_sources:
      diesel_import:
        fuel: diesel
        node: 0
        max_supply: 500.0
        import_cost: 850.0
        storage_capacity: 10000.0
        storage_min: 1000.0
        storage_initial: 5000.0
      gas_pipeline:
        fuel: natural_gas
        node: 0
        max_supply: 300.0
        import_cost: 400.0
        storage_capacity: 2000.0
        storage_min: 200.0
        storage_initial: 1000.0

    fuel_entry_points:
      - node: 0
        fuels: [diesel, natural_gas]
        max_import_rate: 500.0
      - node: 1
        fuels: [diesel]
        max_import_rate: 200.0
      - node: 2
        fuels: [diesel]
        max_import_rate: 150.0

    transport_routes:
      - from_node: 0
        to_node: 1
        distance_km: 150.0
        fuel_params:
          diesel:
            capacity: 200.0
            transport_losses: 0.5
            transport_cost: 10.0
          natural_gas:
            capacity: 100.0
            transport_losses: 0.2
            transport_cost: 5.0
      - from_node: 1
        to_node: 2
        distance_km: 100.0
        fuel_params:
          diesel:
            capacity: 100.0
            transport_losses: 0.5
            transport_cost: 12.0

    primary_energy_infrastructure:
      diesel:
        storage_efficiency: 0.98
        storage_expansion_limit: 2.0
        storage_invest_cost: 50.0
      natural_gas:
        storage_efficiency: 0.95
        storage_expansion_limit: 1.5
        storage_invest_cost: 200.0

    non_electric_demand:
      transport_diesel:
        fuel: diesel
        sector: transport
        demand_per_node: [50.0, 25.0, 15.0]
        criticality: 0.8
      industrial_gas:
        fuel: natural_gas
        sector: industrial
        demand_per_node: [30.0, 10.0, 0.0]
        criticality: 0.9

    generators:
      diesel_gen:
        name: Diesel Generator
        type: Non-renewable
        fuel: Diesel
        rated_power: [30.0, 20.0, 40.0]
        min_power: [0.3, 0.3, 0.3]
        invest_cost: [0, 0, 0]
        invest_max_power: [0, 0, 0]
        fuel_cost: [85.0, 85.0, 85.0]
        fixed_cost: [5.0, 5.0, 5.0]
        maintenance_cost: [3.0, 3.0, 3.0]
        eff_at_rated: [0.38, 0.38, 0.38]
        eff_at_min: [0.30, 0.30, 0.30]
        life_time: [30, 30, 30]
        initial_age: [10, 10, 10]

      gas_turbine:
        name: Gas Turbine
        type: Non-renewable
        fuel: Natural Gas
        rated_power: [25.0, 0.0, 0.0]
        min_power: [0.2, 0.0, 0.0]
        invest_cost: [0, 0, 0]
        invest_max_power: [0, 0, 0]
        fuel_cost: [55.0, 55.0, 55.0]
        fixed_cost: [4.0, 4.0, 4.0]
        maintenance_cost: [2.5, 2.5, 2.5]
        eff_at_rated: [0.42, 0.42, 0.42]
        eff_at_min: [0.32, 0.32, 0.32]
        life_time: [25, 25, 25]
        initial_age: [5, 5, 5]

    technologies:
      solar_pv:
        name: Solar PV
        type: Renewable
        fuel: Solar
        invest_cost: [700000, 700000, 700000]
        invest_max_power: [200, 100, 100]
        Availability: solar_profile.csv
        eff_at_rated: [1.0, 1.0, 1.0]
        degradation_rate: [0.005, 0.005, 0.005]
        lifetime: 25

    battery_technologies:
      li_ion:
        name: Li-Ion
        invest_cost_power: [180000, 180000, 180000]
        invest_cost_energy: [120000, 120000, 120000]
        invest_max_power: [100, 50, 50]
        invest_max_capacity: [400, 200, 200]
        min_duration_hours: 2.0
        max_duration_hours: 8.0
        efficiency_charge: [0.95, 0.95, 0.95]
        efficiency_discharge: [0.95, 0.95, 0.95]
        lifetime: 15

    penalties:
      loss_of_load: 10000000.0
      curtailment: 100.0
      max_curtailment_ratio: 0.05
      fre_penetration_loss: 100.0
      co2_cost: 10.0
      loss_of_fuel_supply: 100.0
      non_electric_demand_loss: 100.0
```

---

## Running the Optimization

```bash
esfex run -c fuel_system.yaml --years 10 -v
```

With `enable_primary_energy: true`, the solver creates additional variables for fuel supply, transport, and storage at each primary energy period. `primary_energy_resolution: 24` optimizes fuel logistics daily while power dispatch remains hourly.

Expected log output:

```
2025-01-01 00:00:00 | INFO     | esfex.runner         | Loading configuration...
2025-01-01 00:00:01 | INFO     | esfex.runner         | Primary energy: ENABLED (2 fuels, 2 sources, 2 routes)
2025-01-01 00:00:02 | INFO     | esfex.runner         | Solving master problem (year 1/10)...
2025-01-01 00:00:15 | INFO     | esfex.runner         | Master problem solved: $12,450,000 (optimal)
...
```

---

## Interpreting Results

### Fuel Consumption by Type

```python
import h5py
import numpy as np

with h5py.File("results/results_island.h5", "r") as f:
    # Year 5 fuel consumption for power generation
    if "detailed_results/island/year_005/fuel_for_power" in f:
        fuel_group = f["detailed_results/island/year_005/fuel_for_power"]
        for gen_idx in fuel_group.keys():
            data = fuel_group[gen_idx][:]
            total_fuel = data.sum()
            print(f"Generator {gen_idx}: total fuel = {total_fuel:.0f} MWh_thermal")
```

### Fuel Storage Inventory

```python
with h5py.File("results/results_island.h5", "r") as f:
    if "detailed_results/island/year_005/fuel_storage" in f:
        storage = f["detailed_results/island/year_005/fuel_storage"]
        for fuel_name in storage.keys():
            levels = storage[fuel_name][:]
            print(f"{fuel_name}: min={levels.min():.0f}t, max={levels.max():.0f}t, "
                  f"avg={levels.mean():.0f}t")
```

### Fuel Cost Breakdown

```python
with h5py.File("results/results_island.h5", "r") as f:
    summary = f["summary_results"]

    total_cost = summary["total_cost"][:].sum()
    print(f"Total system cost (NPV): ${total_cost:,.0f}")

    if "fuel_supply_cost" in summary:
        fuel_cost = summary["fuel_supply_cost"][:].sum()
        print(f"Fuel supply cost: ${fuel_cost:,.0f} ({fuel_cost/total_cost:.1%})")

    if "fuel_transport_cost" in summary:
        transport_cost = summary["fuel_transport_cost"][:].sum()
        print(f"Fuel transport cost: ${transport_cost:,.0f} ({transport_cost/total_cost:.1%})")
```

### Emissions Summary

```python
with h5py.File("results/results_island.h5", "r") as f:
    if "summary_results/total_emissions" in f:
        emissions = f["summary_results/total_emissions"][:]
        for y, e in enumerate(emissions):
            print(f"Year {y+1}: {e:,.0f} tCO2")
        print(f"Cumulative: {emissions.sum():,.0f} tCO2")
```

---

## Tips for Calibrating Fuel Parameters

1. **Start simple**: Begin with a single fuel type (diesel) and verify the model runs correctly before adding natural gas, LNG, or hydrogen.

2. **Validate storage dynamics**: Check that storage levels stay within bounds and that safety stock (`storage_min`) is never violated. If the optimizer frequently hits storage constraints, consider increasing `max_supply` or `storage_capacity`.

3. **Calibrate transport costs**: Transport costs significantly affect where new generation is built. Too-cheap transport leads to all generation at the cheapest fuel node; too-expensive transport leads to distributed (possibly suboptimal) generation.

4. **Non-electric demand priority**: Set `criticality` values carefully. If non-electric demand criticality is too high, the optimizer may shed electrical load before curtailing non-electric fuel supply. If too low, essential services (transport) may be unrealistically curtailed.

5. **Emission factors**: Use nationally reported emission factors. The `emission_factor` is per MWh_thermal when `energy_content > 0`. Verify with: `tCO2_per_tonne = emission_factor * energy_content`.

6. **Temporal resolution**: The `primary_energy_resolution` setting controls the period length for fuel logistics. Values of 24 (daily) or 168 (weekly) are typical. Lower values increase accuracy but also computation time.

7. **Multi-fuel competition**: When multiple fuels serve the same node, the optimizer chooses based on total delivered cost (import + transport + losses). Ensure fuel-generator assignments are correct (each generator uses exactly one fuel type).

---

## Key Takeaways

1. **Supply chain costs matter**: Fuel transport and storage add 10-30% to generation costs beyond the base fuel price, especially for island systems.
2. **Strategic reserves**: The `storage_min` parameter models safety stock requirements. Setting it too high ties up capital in inventory; setting it too low risks fuel shortages during demand peaks.
3. **Import constraints drive investment**: Limited fuel import capacity can make renewable investment economically attractive even at higher upfront cost, because it avoids fuel supply bottlenecks.
4. **Non-electric competition**: Transport and industrial fuel demand competes directly with power generation for limited fuel supply. This competition is invisible in models without primary energy.
5. **Emissions tracking**: Primary energy provides a complete picture of system emissions, including non-electric sectors. This is essential for accurate CO2 budget analysis.
6. **RE value amplified** [**[48]**](../reference/bibliography.md#ref48): Renewable energy reduces fuel dependence, providing cost savings (avoided fuel procurement), security benefits (reduced import vulnerability), and environmental benefits (lower emissions from both electric and displaced non-electric sources).
