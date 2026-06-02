# Single-Node Island System

## Prerequisites

- ESFEX installed: `pip install esfex`
- A working solver (HiGHS is included by default)
- Python 3.10+ with `h5py`, `numpy`, and `matplotlib` for results analysis

---

## Scenario

An isolated island currently relies on diesel generators. We want to plan a 10-year transition toward 80% renewable energy by investing in solar PV, wind turbines, and battery storage.

**System characteristics:**

- 1 node (single bus)
- Peak demand: ~200 MW
- Existing: 250 MW diesel, 50 MW solar PV
- Candidates: Additional solar PV, wind, Li-ion batteries
- Target: 80% renewable energy by year 10
- CO2 budget: 500,000 tonnes/year

---

## Step 1: Create the Configuration

Create `island_system.yaml` with the configuration below.

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
  time_limit: 1800
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
    demand_path: island_demand.xlsx
    demand_scale: 1.0
    demand_growth: 0.02

    nodes:
      adjacency_matrix: [[0]]
      coordinates: [[-76.80, 18.00]]
      names: ["Main Island"]

    generators:
      solar_pv:
        name: Solar PV
        type: Renewable
        fuel: Solar
        rated_power: [50.0]
        min_power: [0.0]
        invest_cost: [750000.0]
        invest_max_power: [500.0]
        fuel_cost: [0.0]
        fixed_cost: [5.0]
        maintenance_cost: [2.0]
        start_up_cost: [0.0]
        decommissioning_cost: [0]
        ramp_up: [1.0]
        ramp_down: [1.0]
        min_up_time: [0]
        min_down_time: [0]
        eff_at_rated: [1.0]
        eff_at_min: [1.0]
        life_time: [25]
        initial_age: [3]
        degradation_rate: [0.005]
        inertia: [0.0]
        Availability: solar_availability.csv

      wind:
        name: Wind Turbines
        type: Renewable
        fuel: Wind
        rated_power: [0.0]
        invest_cost: [1200000.0]
        invest_max_power: [300.0]
        fuel_cost: [0.0]
        fixed_cost: [8.0]
        maintenance_cost: [5.0]
        start_up_cost: [0.0]
        decommissioning_cost: [0]
        ramp_up: [1.0]
        ramp_down: [1.0]
        min_up_time: [0]
        min_down_time: [0]
        eff_at_rated: [1.0]
        eff_at_min: [1.0]
        life_time: [20]
        initial_age: [0]
        degradation_rate: [0.005]
        inertia: [0.0]
        Availability: wind_availability.csv

      diesel:
        name: Diesel Generator
        type: Non-renewable
        fuel: Diesel
        rated_power: [250.0]
        min_power: [0.3]
        invest_cost: [500000.0]
        invest_max_power: [0.0]
        fuel_cost: [85.0]
        fixed_cost: [3.0]
        maintenance_cost: [5.0]
        start_up_cost: [5000.0]
        decommissioning_cost: [100000]
        ramp_up: [0.5]
        ramp_down: [0.5]
        min_up_time: [4]
        min_down_time: [2]
        eff_at_rated: [0.40]
        eff_at_min: [0.30]
        life_time: [30]
        initial_age: [15]
        degradation_rate: [0.01]
        inertia: [5.0]

    batteries:
      li_ion:
        name: Li-Ion Battery
        capacity: [0.0]
        max_charge_power: [0.0]
        max_discharge_power: [0.0]
        charge_efficiency: [0.95]
        discharge_efficiency: [0.95]
        soc_min: [0.10]
        soc_max: [0.95]
        soc_initial: [0.50]
        self_discharge: [0.0001]
        invest_cost_power: [200000.0]
        invest_cost_capacity: [150000.0]
        invest_max_power: [200.0]
        invest_max_capacity: [800.0]
        min_duration_hours: 2.0
        max_duration_hours: 6.0
        life_time: [15]
        maintenance_cost: [1.0]
        spillage: false
        degradation_rate: [0.02]

    penalties:
      LOSS_DEMAND_TRHESHOLD: 10000.0
      curtailment_penalty: 50.0
      loss_reserve_static_penalty: 500.0
      fre_penalty: 600.0

    co2_budget:
      annual_limit: 500000.0

    target_re_penetration: 0.80
    initial_re_penetration: 0.0
    max_curtailment_ratio: 0.05
    discount_rate: 0.08
    MAX_ANNUAL_SYSTEM_COST: 500000000.0
```

### Key Parameters

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `simulation_mode` | `development` | Economic dispatch without unit commitment binaries (faster LP) |
| `resolution_hours` | `1` | Hourly time steps for operational dispatch |
| `rolling_horizon_hours` | `48` | Each operational window solves 48 hours at a time |
| `overlap_hours` | `6` | 6-hour overlap between windows for continuity |
| `representative_days_per_year` | `5` | Master problem uses 5 representative days per year |
| `target_re_penetration` | `0.80` | 80% renewable energy target by the final year |
| `max_curtailment_ratio` | `0.05` | No more than 5% of renewable generation can be curtailed |
| `discount_rate` | `0.08` | 8% discount rate for NPV calculations |

---

## Step 2: Prepare Input Data

### Demand File

Create `island_demand.xlsx` with 8,760 rows (1 year of hourly data) and 1 column (1 node). The values represent load in MW at each hour.

| Hour | Node 0 |
|------|--------|
| 1 | 120.5 |
| 2 | 115.3 |
| ... | ... |
| 8760 | 125.0 |

The demand profile should reflect typical island load patterns: lower overnight (80-120 MW), morning ramp (120-170 MW), afternoon peak (170-200 MW), and evening decline. If you have multiple years of data, concatenate them (17,520 rows for 2 years, etc.).

The `demand_growth: 0.02` parameter applies a 2% annual compound growth to this base profile for subsequent years.

### Availability Files

Create `solar_availability.csv` with 8,760 rows, 1 column, values between 0.0 and 1.0. Each value represents the fraction of rated capacity available at that hour.

```csv
0.0
0.0
0.0
0.0
0.0
0.05
0.25
0.55
0.75
0.85
0.90
0.88
0.85
0.80
0.65
0.40
0.15
0.02
0.0
0.0
0.0
0.0
0.0
0.0
```

This example shows a typical tropical solar day: zero output at night, ramping from sunrise (~06:00) to noon peak (~0.90), then declining to zero by ~18:00. The full file continues this pattern for all 365 days, with seasonal and weather variations.

Create `wind_availability.csv` following the same format. Wind profiles are typically less predictable than solar, with values varying between 0.0 and 1.0 throughout the day and season.

---

## Step 3: Run the Simulation

```bash
# Validate the configuration first (catches errors before the long solve)
esfex validate -c island_system.yaml

# Run the 10-year planning study
esfex run -c island_system.yaml --years 10 --verbose
```

The simulation proceeds in two stages for each year:

1. **Master Problem**: Solves the strategic investment/retirement decisions across all years using representative days
2. **Operational Dispatch**: Solves detailed hourly dispatch for each year using the rolling horizon approach

Expect the full run to take 15-60 minutes depending on your hardware and solver configuration.

---

## Step 4: Interpret Results

### Investment Decisions

```python
import h5py

with h5py.File("results/output.h5", "r") as f:
    investments = f["summary_results/investments"][:]
    print("Year | Solar PV | Wind | Battery Power | Battery Energy")
    for year_data in investments:
        print(f"{year_data}")
```

Expected pattern:

- **Early years (1-3)**: Solar PV investment dominates because it has the lowest investment cost ($750,000/MW vs $1,200,000/MW for wind). Small battery additions begin to manage solar intermittency.
- **Mid years (4-7)**: Wind investment starts for temporal diversity (wind generates when the sun does not). Battery capacity grows to 4-6 hour duration as RE penetration rises above 50%.
- **Late years (8-10)**: Replacement investments may appear as early solar installations degrade. The diesel generator (initial age 15, lifetime 30) approaches retirement age and its effective capacity declines at 1%/year.

### Generation Mix

```python
import numpy as np

with h5py.File("results/output.h5", "r") as f:
    for yr in range(1, 11):
        gen = f[f"detailed_results/island/year_{yr:03d}/gen_output"][:]
        total = gen.sum(axis=(1, 2))  # Sum over nodes and hours
        print(f"Year {yr}: Solar={total[0]:.0f} MWh, "
              f"Wind={total[1]:.0f} MWh, Diesel={total[2]:.0f} MWh")
```

A typical progression might look like:

| Year | Solar (GWh) | Wind (GWh) | Diesel (GWh) | Battery (GWh) | RE Share |
|------|------------|-----------|-------------|--------------|---------|
| 1 | 85 | 0 | 1,300 | 0 | 6% |
| 3 | 350 | 120 | 1,000 | 45 | 32% |
| 5 | 550 | 280 | 680 | 95 | 55% |
| 7 | 700 | 400 | 440 | 130 | 72% |
| 10 | 850 | 500 | 250 | 170 | 84% |

### RE Penetration Trajectory

```python
with h5py.File("results/output.h5", "r") as f:
    re = f["summary_results/re_penetration"][:]
    for yr, pen in enumerate(re, 1):
        print(f"Year {yr}: RE penetration = {pen:.1%}")
```

The RE penetration should follow a roughly linear trajectory from the initial value (~6% from existing 50 MW solar) toward the 80% target. The optimizer determines the most cost-effective pace, constrained by `min_annual_increment` and `max_annual_increment` if configured.

### LCOE Analysis

```python
with h5py.File("results/output.h5", "r") as f:
    # Per-technology LCOE if available
    if "summary_results/lcoe" in f:
        lcoe = f["summary_results/lcoe"][:]
        tech_names = ["Solar PV", "Wind", "Diesel"]
        for name, cost in zip(tech_names, lcoe):
            print(f"{name} LCOE: ${cost:.1f}/MWh")
```

Expected LCOE ranges for this scenario:

| Technology | LCOE ($/MWh) | Notes |
|-----------|-------------|-------|
| Solar PV | 35-55 | Low fuel cost, moderate investment |
| Wind | 50-75 | Higher investment, good capacity factor |
| Diesel | 180-250 | Dominated by fuel cost at $85/MWh |
| Li-Ion Battery | 120-180 | Enables RE integration, reduces curtailment |

The system LCOE (blended cost) should decline over the planning horizon as renewable energy displaces diesel.

### Cost Breakdown

```python
with h5py.File("results/output.h5", "r") as f:
    objectives = f["summary_results/objectives"][:]
    total_npv = sum(obj / (1.08 ** yr) for yr, obj in enumerate(objectives))
    print(f"Total NPV (10 years): ${total_npv:,.0f}")
    print(f"Average annual cost: ${np.mean(objectives):,.0f}")
    print(f"Year 1 cost: ${objectives[0]:,.0f}")
    print(f"Year 10 cost: ${objectives[-1]:,.0f}")
```

### Alternative: Full Python API Workflow

```python
from esfex import load_config
from esfex.runner import Orchestrator

# Load, run, and analyze - no CLI needed
config = load_config("island_system.yaml")
orchestrator = Orchestrator(config, config_path="island_system.yaml")
results = orchestrator.run(years=10, start_year=2025)

# Print year-by-year summary
for yr in results:
    print(f"Year {yr.year}: "
          f"Cost=${yr.objective:,.0f}  "
          f"RE={yr.re_penetration:.1%}  "
          f"CO2={yr.emissions:,.0f}t  "
          f"Load shed={yr.load_shed:.1f} MWh")

# Plot generation stack for the final year
import matplotlib.pyplot as plt
import numpy as np

yr = results[-1]
hours = np.arange(168)  # First week
fig, ax = plt.subplots(figsize=(14, 5))
ax.stackplot(hours,
    yr.gen_output[0, 0, :168],   # Solar
    yr.gen_output[1, 0, :168],   # Wind
    yr.gen_output[2, 0, :168],   # Diesel
    labels=["Solar PV", "Wind", "Diesel"],
    colors=["#f1c40f", "#3498db", "#7f8c8d"])
if yr.bat_discharge is not None:
    ax.stackplot(hours,
        yr.bat_discharge[0, 0, :168],
        labels=["Battery"], colors=["#2ecc71"], alpha=0.7)
ax.plot(hours, yr.demand[0, :168] if yr.demand.ndim == 2 else yr.demand[:168],
    "k-", linewidth=1.5, label="Demand")
ax.set_xlabel("Hour")
ax.set_ylabel("Power (MW)")
ax.set_title(f"Generation Mix - Year {yr.year} (First Week)")
ax.legend(loc="upper right")
plt.tight_layout()
plt.savefig("island_generation_week.png", dpi=150)

# Compare investment trajectories
print("\nInvestment Decisions:")
for yr in results:
    inv = {k: v for k, v in yr.investments.items() if v > 0.1}
    if inv:
        inv_str = ", ".join(f"{k}: {v:.1f} MW" for k, v in inv.items())
        print(f"  Year {yr.year}: {inv_str}")
```

---

## Step 5: Results Analysis Deep Dive

### Curtailment Analysis

With the 5% curtailment cap, the optimizer invests in storage rather than overbuilding renewables.

```python
with h5py.File("results/output.h5", "r") as f:
    for yr in range(1, 11):
        key = f"detailed_results/island/year_{yr:03d}"
        if f"{key}/curtailment" in f:
            curt = f[f"{key}/curtailment"][:]
            gen = f[f"{key}/gen_output"][:]
            # RE generation is generators 0 (solar) and 1 (wind)
            re_gen = gen[0].sum() + gen[1].sum()
            curt_total = curt.sum()
            ratio = curt_total / re_gen if re_gen > 0 else 0
            print(f"Year {yr}: Curtailment = {curt_total:.0f} MWh "
                  f"({ratio:.1%} of RE generation)")
```

If curtailment approaches the 5% cap, it signals that additional storage investment would be beneficial.

### CO2 Emissions Tracking

```python
with h5py.File("results/output.h5", "r") as f:
    emissions = f["summary_results/emissions"][:]
    budget = 500_000  # tonnes/year
    for yr, em in enumerate(emissions, 1):
        status = "WITHIN" if em <= budget else "EXCEEDED"
        print(f"Year {yr}: {em:,.0f} tCO2 ({status} budget)")
```

Emissions should decline steadily as diesel generation is displaced. The CO2 budget constraint ensures the transition stays on track, even if the RE target alone would allow higher emissions.

### Battery Utilization

```python
with h5py.File("results/output.h5", "r") as f:
    for yr in [1, 5, 10]:
        key = f"detailed_results/island/year_{yr:03d}"
        if f"{key}/bat_charge" in f:
            charge = f[f"{key}/bat_charge"][:].sum()
            discharge = f[f"{key}/bat_discharge"][:].sum()
            cycles = discharge / 800  # Approximate full cycles (800 MWh capacity)
            print(f"Year {yr}: Charged={charge:.0f} MWh, "
                  f"Discharged={discharge:.0f} MWh, "
                  f"~{cycles:.0f} full cycles")
```

---

## Key Takeaways

1. **Investment timing**: The optimizer spreads investments across years to match the RE target progression and minimize NPV. Front-loading all investment is rarely optimal due to discounting and technology degradation.
2. **Storage role**: Batteries become essential once RE exceeds ~50% to handle intermittency. The 5% curtailment cap forces storage investment rather than overbuilding RE.
3. **Diesel phase-out**: Existing diesel capacity degrades naturally (1%/year) and may not need active retirement. By year 10, its effective capacity is ~225 MW but its utilization drops below 20%.
4. **Curtailment limit**: Without the 5% cap, the optimizer might overbuild solar by 30-50% and curtail excess, which is wasteful. The constraint drives economically efficient storage sizing.
5. **Cost trade-offs**: Cheaper solar comes first; wind adds value through temporal diversity (evening and nighttime generation). The optimal mix depends on relative availability profiles.
6. **Discount rate sensitivity**: At 8%, the optimizer slightly delays investments. Lower discount rates (3-5%) favor earlier, larger RE investments. See the [Sensitivity Analysis Tutorial](sensitivity-analysis.md) for systematic exploration.

---

## Next Steps

- [Multi-Node Tutorial](multi-node.md) — add transmission constraints and spatial optimization
- [EV Integration](ev-integration.md) — add electric vehicles with V2G capability
- [Primary Energy](primary-energy.md) — model the diesel supply chain
- [Sensitivity Analysis](sensitivity-analysis.md) — quantify the impact of uncertain parameters
