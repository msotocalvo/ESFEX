# Quickstart


## Step 1: Prepare a Configuration File

Create a YAML configuration file `my_system.yaml` defining a single-node island power system with one solar PV generator, one diesel generator, and a lithium-ion battery available for investment.

```yaml
simulation_mode: development
date_start: "01/01/2025 00:00"

temporal:
  resolution_hours: 1
  use_rolling_horizon: true
  rolling_horizon_hours: 48
  overlap_hours: 6

solver:
  name: highs
  threads: 4
  time_limit: 3600
  gap: 0.01
  verbose: false

n1_security:
  enabled: false

master_problem:
  stochastic: false
  representative_days_per_year: 5
  min_day_separation: 7

enable_primary_energy: false

meta_network:
  systems:
    - island

systems:
  island:
    name: island
    demand_path: demand.xlsx
    demand_scale: 1.0

    nodes:
      adjacency_matrix: [[0]]
      coordinates: [[-82.38, 23.13]]
      names: ["Main"]

    generators:
      solar_pv:
        name: Solar PV
        type: Renewable
        fuel: Solar
        rated_power: [50.0]         # MW per node
        invest_cost: [800000.0]     # $/MW
        invest_max_power: [500.0]   # MW max investment
        life_time: [25]
        initial_age: [0]
        Availability: solar_availability.csv
        # ... other fields use defaults

      diesel:
        name: Diesel
        type: Non-renewable
        fuel: Diesel
        rated_power: [100.0]
        fuel_cost: [80.0]           # $/MWh
        life_time: [30]
        initial_age: [10]

    batteries:
      li_ion:
        name: Li-Ion Storage
        capacity: [0.0]             # MWh (start with none)
        max_charge_power: [0.0]
        max_discharge_power: [0.0]
        invest_cost_power: [200000.0]
        invest_cost_capacity: [150000.0]
        invest_max_power: [200.0]
        invest_max_capacity: [800.0]
        life_time: [15]

    penalties:
      LOSS_DEMAND_TRHESHOLD: 10000.0
      curtailment_penalty: 50.0
      loss_reserve_static_penalty: 500.0
      fre_penalty: 600.0

    co2_budget:
      annual_limit: 1000000.0

    target_re_penetration: 0.80
    initial_re_penetration: 0.0
    max_curtailment_ratio: 0.05
```

### Understanding the Key Sections

| Section | Purpose |
|---------|---------|
| `simulation_mode` | `development` uses economic dispatch (LP). Use `unit_commitment` for binary on/off decisions (MIP, slower). |
| `temporal` | Controls time resolution and the rolling horizon window size. A 48-hour window with 6-hour overlap is a good default. |
| `solver` | Selects the optimization solver. HiGHS is free and works well for most problems. |
| `master_problem` | Configures the capacity expansion stage. 5 representative days is a starting point; increase for more accuracy. |
| `generators` | Defines each generator type. `rated_power` is per-node (array). `invest_max_power` limits new capacity. |
| `batteries` | Starting capacity of 0 means no existing storage; the optimizer will invest if economical. |
| `penalties` | Soft constraint costs. `LOSS_DEMAND_TRHESHOLD` is the cost of unserved energy ($/MWh). |
| `target_re_penetration` | The renewable energy fraction target at the end of the planning horizon (0.80 = 80%). |
| `max_curtailment_ratio` | Maximum allowable curtailment as a fraction of renewable generation (0.05 = 5%). |

!!! note "Demand File"
    The demand file (`demand.xlsx`) should be an Excel file with one column per node and one row per hour (8760 rows for one year). Values are in MW. Column headers should match the node names defined in `nodes.names`. If you have multi-year demand, include 8760 rows per year stacked vertically.

!!! note "Availability File"
    The availability file (`solar_availability.csv`) contains hourly capacity factors (0.0 to 1.0) for the renewable generator. It should have 8760 rows per year. This file represents the fraction of rated power available at each hour due to weather conditions.

---


## Step 2: Validate the Configuration

```bash
esfex validate -c my_system.yaml
```

Expected output:

```
Validating: my_system.yaml
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

Common validation errors:

- Missing required fields (e.g., `rated_power` not defined for a generator)
- Array length mismatch (e.g., `rated_power` has 2 elements but only 1 node is defined)
- File not found (e.g., `demand.xlsx` does not exist at the specified path)
- Invalid enum values (e.g., `type: renewable` instead of `type: Renewable`)

---


## Step 3: Dry Run

```bash
esfex run -c my_system.yaml --dry-run
```

The dry run loads and validates the configuration, shows the system summary, and reports how many years, windows, and subproblems will be created — without launching any solver.


---


## Step 4: Run the Simulation

```bash
esfex run -c my_system.yaml --years 10 --verbose
```

Execution stages:

1. **Load and validate** the configuration
2. **Load demand data** for all 10 years from the Excel file
3. **Preload availability profiles** for all renewable generators (cached in memory)
4. **Solve the Master Problem** -- capacity expansion over 10 years using representative days
5. **For each year (1 through 10):**
    - Apply cumulative investment and retirement decisions to the system configuration
    - Solve **operational dispatch** using a rolling horizon with the configured window size and overlap
    - Compute derived metrics (capacity factor, LCOE, VALLCOE)
6. **Export results** to HDF5

### Expected Console Output

When running with `--verbose`, you will see output similar to:

```
ESFEX - Power System Optimization
Configuration: my_system.yaml
Mode: development
Solver: highs

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

Loading demand data... OK (8760 x 1 per year, 10 years)
Preloading availability profiles... OK (2 files cached)
Solving Master Problem (10 years, 5 representative days)...
  Master Problem solved in 12.3s - Total NPV cost: $145,234,567

Year  1/10 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%  0:02:15
  Investments applied: Solar PV +87.3 MW, Li-Ion Storage +24.1 MW / 96.4 MWh
  Operational cost: $8,456,789  RE: 62.3%  Load shed: 0.00%

Year  2/10 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%  0:02:08
  Operational cost: $7,891,234  RE: 68.1%  Load shed: 0.00%

...

Year 10/10 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%  0:01:55
  Operational cost: $5,234,567  RE: 81.2%  Load shed: 0.00%

Optimization completed successfully!
Results saved to: ./results
```

### Output Fields

| Field | Meaning |
|-------|---------|
| Master Problem NPV cost | Net present value of total system cost over all years |
| Investments applied | Cumulative new capacity installed by that year |
| Operational cost | Total annual dispatching cost for that year |
| RE | Renewable energy penetration (fraction of total demand served) |
| Load shed | Fraction of unserved demand (0.00% = all demand met) |

### Typical Runtimes

| System size | Years | Approximate runtime |
|-------------|-------|-------------------|
| 1 node, 2 generators | 10 | 5--15 minutes |
| 1 node, 5 generators | 25 | 20--60 minutes |
| 5 nodes, 10 generators | 25 | 1--4 hours |
| 10+ nodes, unit commitment | 25 | 4--24 hours |

Runtimes depend on the solver, number of rolling horizon windows, temporal resolution, and available CPU cores.


---


## Step 5: Inspect Results

Results are saved in HDF5 format and can be exported to CSV, Excel, or JSON.

### Export to CSV

```bash
esfex export -r results/output.h5 -f csv -o results/csv/
```

This creates a directory structure with one CSV file per data group:

```
results/csv/
├── summary_results/
│   ├── investments.csv
│   ├── retirements.csv
│   └── annual_summary.csv
└── detailed_results/
    └── island/
        ├── year_001/
        │   ├── gen_output.csv
        │   ├── bat_charge.csv
        │   ├── bat_discharge.csv
        │   ├── bat_soc.csv
        │   ├── load_shed.csv
        │   └── prices.csv
        ├── year_002/
        │   └── ...
        └── ...
```

### Export to Excel

```bash
esfex export -r results/output.h5 -f excel -o results/
```

### Load Results in Python

```python
import h5py
import numpy as np

with h5py.File("results/output.h5", "r") as f:
    # List all groups
    print("Groups:", list(f.keys()))

    # Read generation data for year 1
    gen_output = f["detailed_results/island/year_001/gen_output"][:]
    print(f"Generation shape: {gen_output.shape}")  # (generators, nodes, hours)

    # Read investment decisions
    investments = f["summary_results/investments"][:]
    print(f"Investments: {investments}")
```

### Interpreting Results

After a simulation completes, the key quantities to examine are:

| Result | Location in HDF5 | Shape | Unit |
|--------|-------------------|-------|------|
| Generator output | `detailed_results/{system}/year_{N}/gen_output` | (generators, nodes, hours) | MW |
| Battery charge | `detailed_results/{system}/year_{N}/bat_charge` | (batteries, nodes, hours) | MW |
| Battery discharge | `detailed_results/{system}/year_{N}/bat_discharge` | (batteries, nodes, hours) | MW |
| Battery SOC | `detailed_results/{system}/year_{N}/bat_soc` | (batteries, nodes, hours) | MWh |
| Load shedding | `detailed_results/{system}/year_{N}/load_shed` | (nodes, hours) | MW |
| Electricity prices | `detailed_results/{system}/year_{N}/prices` | (nodes, hours) | $/MWh |
| Investments | `summary_results/investments` | Varies | MW or MWh |
| Annual summary | `summary_results/annual_summary` | (years,) | Various |

**Sanity checks:**

- **Load shedding** near zero in most hours. Persistent shedding indicates insufficient generation or transmission capacity.
- **RE penetration** increasing year over year toward the target.
- **Battery SOC** cycling daily (charge during solar hours, discharge in the evening).
- **Electricity prices** reflecting marginal generation cost. High prices indicate system stress.

---


## Alternative: Run Entirely from Python


### Basic Python Workflow

```python
from esfex import load_config
from esfex.runner import Orchestrator

# 1. Load and validate configuration
config = load_config("my_system.yaml")

# 2. Create the orchestrator
orchestrator = Orchestrator(
    config=config,
    output_dir="./results",
    config_path="my_system.yaml"  # For resolving relative paths
)

# 3. Run the simulation
results = orchestrator.run(years=10, start_year=2025)

# 4. Analyze results in memory (no HDF5 needed)
for yr in results:
    print(f"Year {yr.year}: "
          f"cost=${yr.objective:,.0f}, "
          f"RE={yr.re_penetration:.1%}, "
          f"CO2={yr.emissions:,.0f} t")
```

### Working with YearResults

Each `YearResults` object contains all simulation data as NumPy arrays:

```python
yr = results[0]  # First year

# Generation output: shape (generators, nodes, hours)
import numpy as np

total_solar = yr.gen_output[0].sum()  # Total solar generation (MWh)
total_diesel = yr.gen_output[1].sum()  # Total diesel generation (MWh)
print(f"Solar: {total_solar:,.0f} MWh, Diesel: {total_diesel:,.0f} MWh")

# Battery operation: shape (batteries, nodes, hours)
if yr.bat_charge is not None:
    total_charged = yr.bat_charge.sum()
    total_discharged = yr.bat_discharge.sum()
    print(f"Battery throughput: {total_charged:,.0f} MWh charged, "
          f"{total_discharged:,.0f} MWh discharged")

# Investment decisions
for key, value in yr.investments.items():
    if value > 0:
        print(f"  Investment: {key} = {value:.1f} MW")

# Electricity prices: shape (nodes, hours) or (hours,)
if yr.prices is not None:
    avg_price = yr.prices.mean()
    print(f"Average electricity price: ${avg_price:.2f}/MWh")
```

### Plotting Results

```python
import matplotlib.pyplot as plt

yr = results[4]  # Year 5
hours = range(len(yr.gen_output[0, 0, :]))

fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

# Generation stack
axes[0].stackplot(hours,
    yr.gen_output[0, 0, :],  # Solar
    yr.gen_output[1, 0, :],  # Diesel
    labels=["Solar PV", "Diesel"],
    colors=["#f39c12", "#7f8c8d"])
axes[0].plot(hours, yr.demand[0, :] if yr.demand.ndim == 2 else yr.demand,
    color="black", linewidth=1.5, label="Demand")
axes[0].set_ylabel("Power (MW)")
axes[0].legend()
axes[0].set_title(f"Generation Mix - Year {yr.year}")

# Battery SOC
if yr.bat_soc is not None:
    axes[1].plot(hours, yr.bat_soc[0, 0, :], color="#2ecc71")
    axes[1].set_ylabel("Battery SOC (MWh)")
    axes[1].set_xlabel("Hour")

plt.tight_layout()
plt.savefig("generation_mix.png", dpi=150)
plt.show()
```

### Multi-Year Summary

```python
import pandas as pd

summary = pd.DataFrame([{
    "year": yr.year,
    "total_cost": yr.objective,
    "re_penetration": yr.re_penetration,
    "co2_emissions": yr.emissions,
    "load_shed": yr.load_shed,
    "total_generation": yr.total_generation,
} for yr in results])

print(summary.to_string(index=False))
summary.to_csv("simulation_summary.csv", index=False)
```

### Modifying Configuration Programmatically

```python
from esfex import load_config

config = load_config("my_system.yaml")

# Change solver
config.solver.name = "gurobi"
config.solver.threads = 8

# Modify generator parameters
island = config.systems["island"]
island.generators["solar_pv"].invest_max_power = [1000.0]  # Double solar potential

# Change RE target
island.target_re_penetration = 0.95

# Run with modified config
orchestrator = Orchestrator(config=config)
results = orchestrator.run(years=15)
```

### Running Parameter Sweeps

```python
from esfex import load_config
from esfex.runner import Orchestrator
import pandas as pd

re_targets = [0.60, 0.70, 0.80, 0.90, 1.00]
sweep_results = []

for target in re_targets:
    config = load_config("my_system.yaml")
    config.systems["island"].target_re_penetration = target

    orch = Orchestrator(config=config, output_dir=f"./results/re_{int(target*100)}")
    results = orch.run(years=10)

    final_year = results[-1]
    sweep_results.append({
        "re_target": target,
        "actual_re": final_year.re_penetration,
        "total_cost": final_year.objective,
        "solar_mw": sum(v for k, v in final_year.investments.items() if "solar" in k.lower()),
        "battery_mw": sum(v for k, v in final_year.investments.items() if "battery" in k.lower()),
    })

df = pd.DataFrame(sweep_results)
print(df.to_string(index=False))
```

---


## Step 6: Launch the GUI Editor (Optional)

```bash
pip install "esfex[gui]"
esfex studio -c my_system.yaml
```

The GUI provides:

- **Interactive map** -- Place nodes, generators, batteries, and transmission lines on an OpenStreetMap-based map by clicking
- **Property forms** -- Edit all equipment parameters (rated power, costs, lifetimes) through structured forms with validation
- **Polyline transmission lines** -- Draw transmission lines with waypoints that follow geographic routes
- **Multi-system management** -- Create and switch between multiple interconnected power systems
- **Resource wizards** -- Generate solar PV and wind availability profiles from geographic data
- **Validation** -- Run configuration validation directly from the GUI before exporting
- **Export to YAML** -- Save the visual system design as a YAML configuration file ready for simulation

---


## Next Steps


### Learn the Model

- [Core Concepts](concepts.md) -- Understand the two-stage optimization, rolling horizon, and penalty structure
- [System Architecture](architecture.md) -- Learn how the Python orchestrator, Julia backend, and bridge layer interact
- [Mathematical Formulation](../formulation/overview.md) -- Read the full mathematical description of the optimization model

### Build Real Systems

- [Single-System Tutorial](../tutorials/single-system.md) -- Detailed walkthrough of an island power system with multiple generator types
- [Multi-Node Tutorial](../tutorials/multi-node.md) -- Add transmission lines and DC power flow between nodes
- [Storage Optimization Tutorial](../tutorials/storage.md) -- Deep dive into battery sizing and operation

### Advanced Features

- [Configuration Guide](../user-guide/configuration.md) -- Full reference for every YAML field
- [EV Integration](../tutorials/ev-integration.md) -- Model electric vehicle fleet adoption and V2G
- [Stochastic Planning](../tutorials/stochastic.md) -- Handle demand and availability uncertainty with scenarios
- [Near-Optimal Alternatives](../tutorials/mga.md) -- Explore the near-optimal investment space with MGA and SPORES
- [Sensitivity Analysis](../tutorials/sensitivity.md) -- Quantify parameter uncertainty impact using Sobol indices
- [Primary Energy](../tutorials/primary-energy.md) -- Model fuel supply chains with storage and transport
