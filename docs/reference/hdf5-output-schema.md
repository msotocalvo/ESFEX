# HDF5 Output Schema

## File Structure Overview

```
results.h5
├── [Root Attributes]              # Metadata
├── system_configuration/          # Static system definition
│   ├── generators/                # Generator parameters
│   └── batteries/                 # Battery parameters
├── summary_results/               # Per-year summary (expandable)
├── demand/                        # Input demand data
│   └── year_YYYY_base_demand      # Per-year demand
├── cost_breakdown/                # Granular cost decomposition
│   └── year_YYYY/                 # Per-year cost components (attrs)
└── detailed_results/              # Full time-series results
    └── year_YYYY_threshold_0/     # Per-year detailed data
        ├── generation/            # Generator output
        ├── gen_status/            # On/off status (UC mode)
        ├── gen_startup/           # Startup events
        ├── gen_shutdown/          # Shutdown events
        ├── battery_charge/        # Charging power
        ├── battery_discharge/     # Discharging power
        ├── battery_soc/           # State of charge
        ├── battery_spillage/      # Energy spillage
        ├── battery_capacity_factor/ # Battery CF
        ├── battery_lcoe/          # Battery LCOE
        ├── battery_vallcoe/       # Battery VALLCOE
        ├── reservoir_level/       # Reservoir water level
        ├── reservoir_spillage/    # Reservoir overflow
        ├── reservoir_pump/        # Pump-back power
        ├── gen_investment_power/   # Generator investments
        ├── bat_investment_power/   # Battery power investments
        ├── bat_investment_capacity/ # Battery energy investments
        ├── capacity_factor/       # Generator CF
        ├── lcoe/                  # Generator LCOE
        ├── vallcoe/               # Generator VALLCOE
        ├── technology_selling_prices/ # Revenue analysis
        └── [scalar datasets]      # System-level time series
```

## Root Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `creation_date` | string | ISO 8601 creation timestamp |
| `num_nodes` | int | Number of network nodes |
| `num_years` | int | Number of simulation years |
| `export_type` | string | `"incremental_results"` |
| `temporal_resolution_hours` | int | Time step (hours) |
| `simulation_mode` | string | `"development"` or `"unit_commitment"` |
| `years_range` | string | Format: `"YYYY-ZZZZ"` |
| `target_re` | float | RE penetration target |
| `export_complete` | bool | Whether export finished successfully |
| `last_update` | string | ISO 8601 last update timestamp |
| `export_timestamp` | string | ISO 8601 export timestamp |

---

## system_configuration/

Static system configuration stored once at file creation.

### system_configuration/generators/generator_{g}/

Per-generator attributes stored as HDF5 attributes:

| Attribute | Type | Description |
|-----------|------|-------------|
| `name` | string | Generator name |
| `type` | string | "Renewable" or "Non-renewable" |
| `fuel` | string | Fuel type |
| `rated_power` | float array | Per-node rated power (MW) |
| `invest_cost` | float array | Per-node investment cost ($/MW) |
| `life_time` | int array | Per-node lifetime (years) |
| `initial_age` | int array | Per-node initial age (years) |

### system_configuration/batteries/battery_{b}/

Per-battery attributes (same structure as generators, plus battery-specific fields).

---

## summary_results/

Expandable arrays that grow as each year completes. One row per simulated year.

| Dataset | Shape | Type | Description |
|---------|-------|------|-------------|
| `year` | (num_years,) | int | Simulation year |
| `threshold` | (num_years,) | int | Threshold iteration |
| `feasible` | (num_years,) | int | 1 if feasible, 0 if not |
| `total_cost` | (num_years,) | float | Total annual cost ($) |
| `renewable_penetration` | (num_years,) | float | RE penetration (0-1) |
| `co2_emissions` | (num_years,) | float | Annual CO2 (tonnes) |
| `loss_of_load` | (num_years,) | float | Total unserved energy (MWh) |

---

## demand/

### demand/year_YYYY_base_demand

| Property | Value |
|----------|-------|
| Shape | (hours,) or (num_nodes, hours) |
| Type | float64 |
| Units | MW |
| Description | Base demand profile for the year |

---

## cost_breakdown/

Granular decomposition of the annual system cost into 27 individual components. Each year's costs are stored as attributes on a `/cost_breakdown/year_YYYY/` group, accumulated across rolling horizon windows.

This group is present when the optimizer exports per-component cost data. The [Financial Analysis](../api/models-financial-analysis.md) module reads this group directly; when it is absent, costs are recalculated from generation output and system configuration.

### cost_breakdown/year_YYYY/ (Attributes)

| Attribute | Type | Units | Description |
|-----------|------|-------|-------------|
| `fuel_cost` | float | $ | Fuel consumption cost |
| `fixed_om_cost` | float | $ | Fixed O&M cost |
| `maintenance_cost` | float | $ | Variable maintenance cost |
| `startup_cost` | float | $ | Generator startup cost |
| `battery_maintenance_cost` | float | $ | Battery O&M cost |
| `battery_degradation_cost` | float | $ | Battery degradation cost |
| `load_shedding_cost` | float | $ | Cost of unserved energy |
| `curtailment_cost` | float | $ | Renewable curtailment penalty |
| `reserve_static_cost` | float | $ | Static reserve deficit penalty |
| `reserve_dynamic_cost` | float | $ | Dynamic reserve deficit penalty |
| `co2_emission_cost` | float | $ | Carbon emission cost |
| `fre_penetration_cost` | float | $ | RE penetration shortfall penalty |
| `inertia_cost` | float | $ | System inertia deficit penalty |
| `soc_violation_cost` | float | $ | Battery SOC violation penalty |
| `transfer_margin_cost` | float | $ | Transfer margin deficit penalty |
| `v2g_compensation` | float | $ | V2G compensation (negative = credit) |
| `flexible_demand_benefit` | float | $ | Demand flexibility benefit (negative = credit) |
| `investment_cost` | float | $ | Capital investment cost |
| `electrolyzer_cost` | float | $ | Electrolyzer operation cost |
| `converter_cost` | float | $ | AC/DC converter cost |
| `spillage_cost` | float | $ | Battery spillage penalty |
| `delay_retirement_cost` | float | $ | Delayed retirement cost |
| `reservoir_spillage_cost` | float | $ | Reservoir overflow penalty |
| `demand_shift_cost` | float | $ | Demand shifting cost |
| `rooftop_curtailment_cost` | float | $ | Rooftop solar curtailment penalty |
| `npv_penalty_cost` | float | $ | NPV lifecycle penalty |
| `reservoir_invest_cost` | float | $ | Reservoir capacity investment cost |
| `total` | float | $ | Sum of all cost components |

### Reading Cost Breakdown

```python
import h5py

with h5py.File("results_system.h5", "r") as f:
    if "cost_breakdown" in f:
        for year_key in sorted(f["cost_breakdown"].keys()):
            attrs = dict(f[f"cost_breakdown/{year_key}"].attrs)
            print(f"{year_key}: total=${attrs['total']:,.0f}")
            print(f"  Fuel: ${attrs['fuel_cost']:,.0f}")
            print(f"  CO2:  ${attrs['co2_emission_cost']:,.0f}")
```

---

## detailed_results/year_YYYY_threshold_0/

Full time-series results for each year. The `threshold_0` suffix is the convergence threshold iteration.

### Attributes (Per-Year Metadata)

| Attribute | Type | Description |
|-----------|------|-------------|
| `year` | int | Simulation year |
| `threshold` | int | Threshold iteration index |
| `feasible` | int | Feasibility flag |
| `total_cost` | float | Total annual cost ($) |
| `renewable_penetration` | float | Final RE penetration |
| `co2_emissions` | float | Annual CO2 emissions (tonnes) |
| `solve_time` | float | Total solve time (seconds) |
| `total_generation` | float | Total energy generated (MWh) |
| `total_demand` | float | Total energy demand (MWh) |
| `load_shed` | float | Total unserved energy (MWh) |

Investment/retirement attributes (per generator/battery):

| Pattern | Description |
|---------|-------------|
| `investment_gen_{name}_node_{n}` | Generator investment at node n (MW) |
| `investment_bat_{name}_power_node_{n}` | Battery power investment (MW) |
| `investment_bat_{name}_capacity_node_{n}` | Battery energy investment (MWh) |
| `retirement_gen_{name}_node_{n}` | Generator retirement flag |
| `retirement_bat_{name}_node_{n}` | Battery retirement flag |

### Generation Data

#### generation/{generator_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | MW |
| Description | Generator output power per node per hour |

#### gen_status/{generator_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | Binary (0/1) or continuous (0-1) |
| Description | Generator commitment status (UC mode: binary; ED mode: continuous) |

#### gen_startup/{generator_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | Binary (0/1) |
| Description | Startup events |

#### gen_shutdown/{generator_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | Binary (0/1) |
| Description | Shutdown events |

#### curtailment

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | MW |
| Description | Aggregated renewable energy curtailment |

### Battery Data

#### battery_charge/{battery_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | MW |
| Description | Charging power |

#### battery_discharge/{battery_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | MW |
| Description | Discharging power |

#### battery_soc/{battery_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | MWh |
| Description | State of charge |

#### battery_spillage/{battery_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | MW |
| Description | Energy spillage (excess energy discarded) |

#### battery_capacity_factor/{battery_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | Fraction (0-1) |
| Description | Battery capacity factor |

#### battery_lcoe/{battery_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | $/MWh |
| Description | Levelized cost of storage |

#### battery_vallcoe/{battery_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | $/MWh |
| Description | Value-adjusted LCOS |

### Reservoir Data

#### reservoir_level/{generator_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours+1) |
| Type | float64 |
| Units | MWh-eq |
| Description | Reservoir water level (includes initial state, hence hours+1) |

#### reservoir_spillage/{generator_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | MW-eq |
| Description | Uncontrolled water overflow |

#### reservoir_pump/{generator_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | MW |
| Description | Pump-back power consumed (demand-side load) |

### Investment Data

#### gen_investment_power/{generator_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes,) |
| Type | float64 |
| Units | MW |
| Description | New generation capacity installed |

#### bat_investment_power/{battery_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes,) |
| Type | float64 |
| Units | MW |
| Description | New battery power capacity |

#### bat_investment_capacity/{battery_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes,) |
| Type | float64 |
| Units | MWh |
| Description | New battery energy capacity |

### Performance Metrics

#### capacity_factor/{generator_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | Fraction (0-1) |
| Description | Generator capacity factor (output / rated) |

#### lcoe/{generator_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | $/MWh |
| Description | Levelized cost of energy |

#### vallcoe/{generator_name}

| Property | Value |
|----------|-------|
| Shape | (num_nodes, hours) |
| Type | float64 |
| Units | $/MWh |
| Description | Value-adjusted levelized cost of energy |

### System-Level Time Series

| Dataset | Shape | Units | Description |
|---------|-------|-------|-------------|
| `demand` | (num_nodes, hours) | MW | Demand profile |
| `reserve_static` | (num_nodes, hours) | MW | Static reserve provided |
| `reserve_dynamic` | (num_nodes, hours) | MW | Dynamic reserve provided |
| `loss_of_reserve_static` | (num_nodes, hours) | MW | Static reserve deficit |
| `loss_of_reserve_dynamic` | (num_nodes, hours) | MW | Dynamic reserve deficit |
| `loss_load` | (num_nodes, hours) | MW | Load shedding |
| `CO2_emissions` | (num_nodes, hours) | tonnes | Hourly CO2 emissions |
| `voltage_angle` | (num_nodes, hours) | radians | Bus voltage angles |

### Economic Data

| Dataset | Shape | Units | Description |
|---------|-------|-------|-------------|
| `electricity_prices` | (hours,) | $/MWh | System-average price |
| `electricity_prices_energy` | (hours,) | $/MWh | Energy component only |
| `nodal_electricity_prices` | (num_nodes, hours) | $/MWh | Locational marginal prices |
| `nodal_electricity_prices_congestion` | (num_nodes, hours) | $/MWh | Congestion component |

### Network Data

| Dataset | Shape | Units | Description |
|---------|-------|-------|-------------|
| `power_flow` | (num_nodes, num_nodes, hours) | MW | Power flow between nodes |
| `transfer_investment` | (num_nodes, num_nodes) | MW | Transmission investment |
| `transfer_margin` | (num_nodes, num_nodes, hours) | MW | Transfer margin used |

### EV Data

| Dataset | Shape | Units | Description |
|---------|-------|-------|-------------|
| `EV_charging` | (num_nodes, hours) | MW | EV charging demand |
| `EV_V2G` | (num_nodes, hours) | MW | Vehicle-to-grid power |
| `EV_soc` | (num_nodes, hours) | MWh | EV fleet SOC |
| `EV_loss` | (num_nodes, hours) | MW | Unmet EV demand |

### Other

#### loss_of_inertia

| Property | Value |
|----------|-------|
| Shape | (hours,) |
| Type | float64 |
| Units | GW*s |
| Description | System inertia deficit |

#### technology_selling_prices/{technology_name}

Per-technology revenue analysis.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `total_generation` | float | Total generation (MWh) |
| `total_revenue` | float | Total revenue ($) |
| `average_selling_price` | float | Weighted average price ($/MWh) |
| `technology_type` | string | Generator type |

**Dataset: `prices_weights`**

| Property | Value |
|----------|-------|
| Shape | (n_entries, 3) |
| Type | float64 |
| Columns | [price ($/MWh), generation (MW), timestep (h)] |
| Description | Price-quantity pairs for revenue decomposition |

---

## Virtual Generator and Battery Datasets

Virtual generators and batteries from technology investments appear in the HDF5 output **after** the original physical units.

### Ordering Convention

Dataset ordering within each group follows the adapter ordering:

1. **Original generators** (from `sys.generators` in config) --- e.g., "Diesel_1", "Solar_PV", "Wind"
2. **Virtual generators** (from technology investments) --- e.g., "Investment Solar PV", "Investment Wind"
3. **Original batteries** (from `sys.batteries` in config) --- e.g., "Li_Ion_1"
4. **Virtual batteries** (from battery technology investments) --- e.g., "Investment Battery"

Virtual units use the same dataset structure as their physical counterparts.

### Identifying Virtual Units

Virtual units are identified by:

- Name prefix: always starts with `"Investment "`
- The `system_configuration/generators/generator_{g}/` attributes: virtual units have `initial_age = 0` and their `rated_power` reflects the cumulative technology investment up to that year

---

## Dataset Dimensions and Data Types

| Category | Dataset Pattern | Shape | dtype | Units |
|----------|----------------|-------|-------|-------|
| Generation | `generation/{name}` | (N, H) | float64 | MW |
| Status | `gen_status/{name}` | (N, H) | float64 | 0-1 |
| Startup | `gen_startup/{name}` | (N, H) | float64 | 0/1 |
| Shutdown | `gen_shutdown/{name}` | (N, H) | float64 | 0/1 |
| Curtailment | `curtailment` | (N, H) | float64 | MW |
| Bat. charge | `battery_charge/{name}` | (N, H) | float64 | MW |
| Bat. discharge | `battery_discharge/{name}` | (N, H) | float64 | MW |
| Bat. SOC | `battery_soc/{name}` | (N, H) | float64 | MWh |
| Bat. spillage | `battery_spillage/{name}` | (N, H) | float64 | MW |
| Reservoir level | `reservoir_level/{name}` | (N, H+1) | float64 | MWh-eq |
| Reservoir spill | `reservoir_spillage/{name}` | (N, H) | float64 | MW-eq |
| Reservoir pump | `reservoir_pump/{name}` | (N, H) | float64 | MW |
| Gen. investment | `gen_investment_power/{name}` | (N,) | float64 | MW |
| Bat. invest. power | `bat_investment_power/{name}` | (N,) | float64 | MW |
| Bat. invest. energy | `bat_investment_capacity/{name}` | (N,) | float64 | MWh |
| Capacity factor | `capacity_factor/{name}` | (N, H) | float64 | 0-1 |
| LCOE | `lcoe/{name}` | (N, H) | float64 | $/MWh |
| VALLCOE | `vallcoe/{name}` | (N, H) | float64 | $/MWh |
| Demand | `demand` | (N, H) | float64 | MW |
| Power flow | `power_flow` | (N, N, H) | float64 | MW |
| Prices | `electricity_prices` | (H,) | float64 | $/MWh |
| Nodal prices | `nodal_electricity_prices` | (N, H) | float64 | $/MWh |
| Loss of load | `loss_load` | (N, H) | float64 | MW |

Where **N** = `num_nodes`, **H** = total hours in the year (typically 8760).

---

## Derived Metrics Formulas

### Capacity Factor

$$CF_g = \frac{\sum_{t=1}^{H} P_{g,t}}{P_{rated,g} \times H}$$

Where $P_{g,t}$ is the generator output at hour $t$ and $P_{rated,g}$ is the rated power.

### LCOE (Levelized Cost of Energy)

$$LCOE_g = \frac{C_{capex,g} \times CRF + C_{opex,g} + C_{fuel,g}}{E_{annual,g}}$$

Where:

- $C_{capex,g}$ = total capital cost ($)
- $CRF = \frac{r(1+r)^n}{(1+r)^n - 1}$ = capital recovery factor
- $C_{opex,g}$ = annual O&M cost ($)
- $C_{fuel,g}$ = annual fuel cost ($)
- $E_{annual,g}$ = annual generation (MWh)

### VALLCOE (Value-Adjusted LCOE)

$$VALLCOE_g = LCOE_g - \frac{\sum_{t} \lambda_t \cdot P_{g,t}}{\sum_{t} P_{g,t}}$$

Where $\lambda_t$ is the electricity price at hour $t$. Negative VALLCOE indicates profitability at market prices.

---

## MGA/SPORES Results

When near-optimal alternative generation is enabled (`master_problem.mga.enabled: true`), an additional `/mga/` group contains every alternative. The same layout serves both methods; the `method` and `objective` attrs disambiguate which generator produced each alternative.

```
results.h5
└── mga/
    ├── @method = "mga" | "spores"     # Generation method (Phase-4 attr)
    ├── @objectives = [...]            # Distinct objectives in display order
    ├── @num_alternatives
    ├── @slack_fraction
    ├── @optimal_cost
    ├── alternative_0/                  # Cost-optimal seed
    │   ├── @is_optimal = 1
    │   ├── @objective = "cost_optimal"
    │   ├── gen_investment              # (years x generators x nodes)
    │   ├── bat_power_investment        # (years x batteries x nodes)
    │   ├── bat_capacity_investment     # (years x batteries x nodes)
    │   ├── transfer_investment         # (years x nodes x nodes)
    │   ├── cumulative_gen_capacity     # (years x generators x nodes)
    │   └── re_penetration              # (years,)
    ├── alternative_1/                  # First non-optimal alternative
    │   ├── @objective = "hsj_diversity"          # under method=mga
    │   │   or                "min_total_build"   # under method=spores
    │   └── ... (same datasets as alternative_0)
    └── alternative_K/
        └── ...
```

### mga/ (root attributes)

| Attribute | Type | Description |
|-----------|------|-------------|
| `method` | string | `"mga"` or `"spores"`. Pre-Phase-4 result files default to `"mga"` on read for back-compat |
| `objectives` | string[] | Distinct objectives in display order. Empty list for legacy MGA runs (the historical `"hsj_diversity"` tag is implicit). Useful as a cache for the viewer's legend |
| `num_alternatives` | int | Total alternatives (including cost-optimal) |
| `slack_fraction` | float | Cost slack used (e.g., 0.05 = 5%) |
| `optimal_cost` | float | Cost-optimal objective value ($) |
| `export_timestamp` | string | ISO timestamp when the `/mga/` group was written |
| `years` | int[] | Planning-horizon years (same as the rest of the file) |

### mga/alternative_N/ (Attributes)

| Attribute | Type | Description |
|-----------|------|-------------|
| `alternative_id` | int | 0 = cost-optimal, 1..K = non-optimal alternatives |
| `is_optimal` | int | 1 if cost-optimal, 0 otherwise |
| `cost` | float | Actual system cost ($) |
| `diversity_objective` | float | Objective value at this alternative. For `method=mga` this is the HSJ diversity score; for `method=spores` it is the value of the SPORES objective named by `attrs["objective"]`. Absent for `alternative_0` |
| `objective` | string | Phase-4 tag identifying *which* objective produced this alternative. Values: `"cost_optimal"` (seed), `"hsj_diversity"` (any MGA alt), or one of `"min_total_build"`, `"max_tech_equity"`, `"max_regional_equity"`, `"evolutionary_dist"` (SPORES alts). Pre-Phase-4 files default to `"cost_optimal"` for the seed and `"hsj_diversity"` for the rest on read |

### mga/alternative_N/ (Datasets)

| Dataset | Shape | Type | Units | Description |
|---------|-------|------|-------|-------------|
| `gen_investment` | (years, generators, nodes) | float64 | MW | Generator investment decisions |
| `bat_power_investment` | (years, batteries, nodes) | float64 | MW | Battery power investment |
| `bat_capacity_investment` | (years, batteries, nodes) | float64 | MWh | Battery energy investment |
| `transfer_investment` | (years, nodes, nodes) | float64 | MW | Transmission investment |
| `cumulative_gen_capacity` | (years, generators, nodes) | float64 | MW | Cumulative capacity (with degradation/retirement) |
| `re_penetration` | (years,) | float64 | fraction | RE penetration ratio per year |

### MGA Output File Naming

MGA results are written to `mga_{system_name}.h5`. The main `results_{system_name}.h5` file still contains the cost-optimal solution's full operational results.

---

## Reading HDF5 Results

### Basic Example with h5py

```python
import h5py
import numpy as np

with h5py.File("results_system.h5", "r") as f:
    # --- Metadata ---
    num_years = f.attrs["num_years"]
    num_nodes = f.attrs["num_nodes"]
    print(f"Simulation: {num_years} years, {num_nodes} nodes")

    # --- Summary across all years ---
    years = f["summary_results/year"][:]
    costs = f["summary_results/total_cost"][:]
    re_pen = f["summary_results/renewable_penetration"][:]

    for y, c, r in zip(years, costs, re_pen):
        print(f"  Year {y}: cost=${c:,.0f}, RE={r:.1%}")

    # --- Detailed: generation for a specific year ---
    gen_output = f["detailed_results/year_2030_threshold_0/generation/solar_pv"][:]
    print(f"Solar PV output shape: {gen_output.shape}")  # (num_nodes, 8760)
    print(f"Total solar generation: {gen_output.sum():.0f} MWh")
```

### Reading Investment Decisions

```python
import h5py

with h5py.File("results_system.h5", "r") as f:
    year_grp = f["detailed_results/year_2030_threshold_0"]

    # Read all investment attributes
    for attr_name in year_grp.attrs:
        if attr_name.startswith("investment_"):
            value = year_grp.attrs[attr_name]
            if value > 0:
                print(f"  {attr_name}: {value:.1f} MW")

    # Read generator investment arrays
    inv_grp = year_grp["gen_investment_power"]
    for gen_name in inv_grp:
        inv_data = inv_grp[gen_name][:]  # shape: (num_nodes,)
        total = inv_data.sum()
        if total > 0:
            print(f"  {gen_name}: {total:.1f} MW total investment")
```

### Reading Battery SOC Profiles

```python
import h5py
import matplotlib.pyplot as plt

with h5py.File("results_system.h5", "r") as f:
    soc = f["detailed_results/year_2030_threshold_0/battery_soc/li_ion"][:]
    # Shape: (num_nodes, 8760)

    # Plot SOC for node 0 over the first week
    hours = range(168)
    plt.figure(figsize=(12, 4))
    plt.plot(hours, soc[0, :168])
    plt.xlabel("Hour")
    plt.ylabel("SOC (MWh)")
    plt.title("Battery State of Charge - Node 0, Week 1")
    plt.grid(True)
    plt.show()
```

### Reading Electricity Prices

```python
import h5py
import numpy as np

with h5py.File("results_system.h5", "r") as f:
    year_grp = f["detailed_results/year_2030_threshold_0"]

    # System-average prices
    prices = year_grp["electricity_prices"][:]
    print(f"Average price: ${np.mean(prices):.2f}/MWh")
    print(f"Peak price: ${np.max(prices):.2f}/MWh")

    # Nodal prices (locational marginal prices)
    nodal = year_grp["nodal_electricity_prices"][:]
    print(f"Nodal prices shape: {nodal.shape}")  # (num_nodes, 8760)

    # Price spread between nodes
    price_spread = nodal.max(axis=0) - nodal.min(axis=0)
    print(f"Average nodal spread: ${np.mean(price_spread):.2f}/MWh")
```

### Reading MGA Results

```python
import h5py

with h5py.File("mga_system.h5", "r") as f:
    if "mga" in f:
        mga = f["mga"]
        meta = mga["metadata"]
        n_alts = meta.attrs["num_alternatives"]
        optimal_cost = meta.attrs["optimal_cost"]
        slack = meta.attrs["slack_fraction"]

        print(f"MGA: {n_alts} alternatives, {slack:.0%} slack")
        print(f"Optimal cost: ${optimal_cost:,.0f}")

        for k in range(n_alts):
            alt = mga[f"alternative_{k}"]
            cost = alt.attrs["cost"]
            gen_inv = alt["gen_investment"][:]  # (years, gens, nodes)
            bat_inv = alt["bat_power_investment"][:]
            re_pen = alt["re_penetration"][:]

            print(f"\nAlternative {k}:")
            print(f"  Cost: ${cost:,.0f} ({(cost/optimal_cost - 1)*100:+.1f}%)")
            print(f"  Total gen investment: {gen_inv.sum():.0f} MW")
            print(f"  Total bat investment: {bat_inv.sum():.0f} MW")
            print(f"  Final RE penetration: {re_pen[-1]:.1%}")
```

### Reading Virtual Generator Output

```python
import h5py

with h5py.File("results_system.h5", "r") as f:
    gen_grp = f["detailed_results/year_2035_threshold_0/generation"]

    # List all generators (original + virtual)
    all_gens = list(gen_grp.keys())
    print(f"All generators: {all_gens}")

    # Virtual generators have "Investment" prefix
    for name in all_gens:
        data = gen_grp[name][:]
        total_mwh = data.sum()
        if "Investment" in name:
            print(f"  [VIRTUAL] {name}: {total_mwh:,.0f} MWh")
        else:
            print(f"  [ORIGINAL] {name}: {total_mwh:,.0f} MWh")
```

### With the CLI

```bash
# Export to CSV
esfex export results.h5 --format csv --output-dir ./csv_results/

# Export to Excel
esfex export results.h5 --format excel --output results.xlsx

# Export specific year
esfex export results.h5 --format csv --year 2030
```
