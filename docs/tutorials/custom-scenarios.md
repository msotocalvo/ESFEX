# Custom Scenarios

Custom scenarios are separate simulation runs with different configurations, compared after the fact. This approach supports policy analysis, sensitivity exploration, and stakeholder engagement.

## Custom Scenarios vs. Stochastic Scenarios

| Feature | Custom Scenarios | Stochastic Scenarios |
|---------|-----------------|---------------------|
| **How they run** | Separate simulations, independent | Single optimization, simultaneous |
| **Investment decisions** | Different per scenario | Shared across all scenarios |
| **Use case** | "What if X?" policy questions | "How to hedge?" under uncertainty |
| **Computation** | Linear: N scenarios = N solves | Combined: N scenarios in 1 large solve |
| **Comparison** | Post-hoc (after all runs complete) | Built-in (expected cost weighting) |

Use **custom scenarios** for fundamentally different policy paths ("What if 100% RE?" vs. "What if diesel continues?"). Use **stochastic scenarios** for a single robust plan that hedges against uncertainty.

---

## Demand Growth Scenarios

### Constant Growth Rate

```yaml
systems:
  island:
    demand_growth: 0.03       # 3% annual growth
```

Year y demand is computed as: D_y = D_base x (1 + growth_rate)^(y-1)

| Year | Growth 1% | Growth 3% | Growth 5% |
|------|-----------|-----------|-----------|
| 1 | 100 MW | 100 MW | 100 MW |
| 5 | 104 MW | 113 MW | 122 MW |
| 10 | 109 MW | 130 MW | 155 MW |
| 15 | 115 MW | 151 MW | 198 MW |
| 25 | 127 MW | 203 MW | 339 MW |

### Multi-Year Demand File

For non-uniform growth or demand shape changes over time, provide a multi-year demand file:

```yaml
    demand_path: demand_25years.xlsx   # 219,000 rows (25 x 8,760)
```

Each year's 8,760 hours are concatenated vertically, allowing:

- **Evening peak growth** from EV adoption (hours 18-22 increase faster)
- **Industrial shifts** (weekend demand changes as industry electrifies)
- **Seasonal pattern changes** (more cooling demand from climate change)
- **Non-uniform growth** (rapid early growth, saturation later)

### Creating a Multi-Year Demand File

```python
import numpy as np
import pandas as pd

# Load single-year hourly demand (8,760 values)
base_demand = pd.read_excel("demand_base.xlsx")["demand"].values

# Create 25-year demand with non-uniform growth
years = 25
growth_rates = np.linspace(0.04, 0.01, years)  # 4% early, declining to 1%
demand_all_years = []

for y in range(years):
    multiplier = np.prod(1 + growth_rates[:y+1]) if y > 0 else 1.0
    year_demand = base_demand * multiplier
    demand_all_years.append(year_demand)

demand_flat = np.concatenate(demand_all_years)
pd.DataFrame({"demand": demand_flat}).to_excel("demand_25years.xlsx", index=False)
print(f"Created demand file: {len(demand_flat)} rows ({years} years x 8,760 hours)")
```

---

## Technology Cost Projections

### Static Costs

```yaml
    technologies:
      solar_pv:
        name: Solar PV
        type: Renewable
        fuel: Solar
        invest_cost: [700000, 700000, 700000]     # $/MW (constant)
        invest_max_power: [500, 300, 200]
```

### Declining Cost Scenarios

To model learning curves, create separate configuration files for different cost trajectories:

**scenario_base_costs.yaml** — current costs, no decline:
```yaml
    technologies:
      solar_pv:
        invest_cost: [700000, 700000, 700000]
      wind:
        invest_cost: [1200000, 1200000, 1200000]
    battery_technologies:
      li_ion:
        invest_cost_power: [180000, 180000, 180000]
        invest_cost_energy: [120000, 120000, 120000]
```

**scenario_optimistic_costs.yaml** — aggressive cost reduction:
```yaml
    technologies:
      solar_pv:
        invest_cost: [500000, 500000, 500000]       # 30% cheaper
      wind:
        invest_cost: [900000, 900000, 900000]        # 25% cheaper
    battery_technologies:
      li_ion:
        invest_cost_power: [100000, 100000, 100000]  # 44% cheaper
        invest_cost_energy: [70000, 70000, 70000]    # 42% cheaper
```

Alternatively, use the stochastic framework with `investment_cost_multiplier` and `battery_cost_multiplier` to capture this in a single run.

---

## RE Penetration Targets

### Progressive Targets

```yaml
    target_re_penetration: 0.80       # 80% by final year
    initial_re_penetration: 0.15      # 15% current (set to 0 for auto-calculation)
    min_annual_increment: 0.02        # At least 2%/year increase
    max_annual_increment: 0.10        # At most 10%/year increase
```

Linear ramp from 15% to 80% over the planning horizon:

| Year | Target RE |
|------|-----------|
| 1 | 15% |
| 5 | ~28% |
| 10 | ~42% |
| 15 | ~56% |
| 20 | ~69% |
| 25 | 80% |

`min_annual_increment` ensures steady progress (prevents "hockey stick" deployment). `max_annual_increment` prevents unrealistic construction rates.

### Comparing RE Target Levels

Create scenarios with different ambition levels:

```yaml
# scenario_conservative.yaml
target_re_penetration: 0.40       # 40% RE

# scenario_moderate.yaml
target_re_penetration: 0.60       # 60% RE

# scenario_ambitious.yaml
target_re_penetration: 0.80       # 80% RE

# scenario_100re.yaml
target_re_penetration: 1.00       # 100% RE (most expensive, may need load shedding slack)
```

---

## CO2 Budget Scenarios

### Constant Budget

```yaml
    co2_budget:
      enabled: true
      annual_budget: 500000.0          # tonnes CO2/year (constant)
```

### Declining Budget

```yaml
# scenario_mild_climate.yaml
co2_budget:
  annual_budget: 500000.0
penalties:
  co2_cost: 10.0                      # Low carbon price

# scenario_moderate_climate.yaml
co2_budget:
  annual_budget: 300000.0
penalties:
  co2_cost: 50.0                      # Moderate carbon price

# scenario_strict_climate.yaml
co2_budget:
  annual_budget: 100000.0
penalties:
  co2_cost: 150.0                     # High carbon price
```

---

## Economic Parameters

### Discount Rate

The discount rate impacts the trade-off between upfront investment (RE + storage) and ongoing operational costs (fossil fuel):

```yaml
    discount_rate: 0.05               # 5% for developed economies
    discount_rate: 0.08               # 8% for developing countries
    discount_rate: 0.12               # 12% for high-risk environments
```

Higher discount rates favor technologies with lower upfront costs (diesel) over technologies with higher upfront costs but lower lifetime costs (solar + storage).

### Investment Budget Constraints

```yaml
    MAX_ANNUAL_SYSTEM_COST: 500000000.0   # $500M/year cap
```

This caps annual investment, spreading deployments over time. Lower budgets delay the RE transition but may be more realistic for constrained economies.

---

## Practical Example: Four Scenarios

### Scenario Definitions

| Scenario | RE Target | Demand Growth | Carbon Price | Budget |
|----------|-----------|--------------|-------------|--------|
| BAU (Business as Usual) | 30% | 3% | $0/tCO2 | $200M/yr |
| Green | 80% | 2% | $50/tCO2 | $500M/yr |
| Conservative | 50% | 1% | $10/tCO2 | $300M/yr |
| Aggressive | 100% | 4% | $100/tCO2 | $800M/yr |

### Creating Scenario Configuration Files

Use a base configuration file with scenario-specific overrides.

**base_system.yaml**:

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

master_problem:
  representative_days_per_year: 5

meta_network:
  systems: [island]

systems:
  island:
    name: island
    demand_path: demand.xlsx
    demand_scale: 1.0

    nodes:
      adjacency_matrix:
        - [0, 100, 75]
        - [100, 0, 50]
        - [75, 50, 0]
      coordinates:
        - [-82.38, 23.13]
        - [-81.95, 22.40]
        - [-80.45, 22.07]
      names: ["Node_A", "Node_B", "Node_C"]

    generators:
      diesel:
        name: Diesel
        type: Non-renewable
        fuel: Diesel
        rated_power: [50.0, 30.0, 40.0]
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

    technologies:
      solar_pv:
        name: Solar PV
        type: Renewable
        fuel: Solar
        invest_cost: [700000, 700000, 700000]
        invest_max_power: [500, 300, 200]
        Availability: solar_profile.csv
        eff_at_rated: [1.0, 1.0, 1.0]
        degradation_rate: [0.005, 0.005, 0.005]
        lifetime: 25

      wind:
        name: Wind
        type: Renewable
        fuel: Wind
        invest_cost: [1200000, 1200000, 1200000]
        invest_max_power: [200, 100, 100]
        Availability: wind_profile.csv
        eff_at_rated: [1.0, 1.0, 1.0]
        degradation_rate: [0.002, 0.002, 0.002]
        lifetime: 20

    battery_technologies:
      li_ion:
        name: Li-Ion
        invest_cost_power: [180000, 180000, 180000]
        invest_cost_energy: [120000, 120000, 120000]
        invest_max_power: [200, 100, 100]
        invest_max_capacity: [800, 400, 400]
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
```

ESFEX does not support YAML inheritance natively. A Python script can generate complete scenario files:

```python
import yaml
import copy
from pathlib import Path

# Load base config
with open("base_system.yaml") as f:
    base = yaml.safe_load(f)

# Define scenario modifications
scenarios = {
    "bau": {
        "systems.island.target_re_penetration": 0.30,
        "systems.island.demand_growth": 0.03,
        "systems.island.penalties.co2_cost": 0.0,
        "systems.island.MAX_ANNUAL_SYSTEM_COST": 200000000.0,
        "systems.island.discount_rate": 0.08,
    },
    "green": {
        "systems.island.target_re_penetration": 0.80,
        "systems.island.demand_growth": 0.02,
        "systems.island.penalties.co2_cost": 50.0,
        "systems.island.MAX_ANNUAL_SYSTEM_COST": 500000000.0,
        "systems.island.discount_rate": 0.05,
    },
    "conservative": {
        "systems.island.target_re_penetration": 0.50,
        "systems.island.demand_growth": 0.01,
        "systems.island.penalties.co2_cost": 10.0,
        "systems.island.MAX_ANNUAL_SYSTEM_COST": 300000000.0,
        "systems.island.discount_rate": 0.06,
    },
    "aggressive": {
        "systems.island.target_re_penetration": 1.00,
        "systems.island.demand_growth": 0.04,
        "systems.island.penalties.co2_cost": 100.0,
        "systems.island.MAX_ANNUAL_SYSTEM_COST": 800000000.0,
        "systems.island.discount_rate": 0.04,
    },
}

def set_nested(d, path, value):
    """Set a nested dict value using dot-separated path."""
    keys = path.split(".")
    for key in keys[:-1]:
        d = d[key]
    d[keys[-1]] = value

for name, mods in scenarios.items():
    config = copy.deepcopy(base)
    for path, value in mods.items():
        set_nested(config, path, value)

    output_path = f"scenario_{name}.yaml"
    with open(output_path, "w") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)
    print(f"Created {output_path}")
```

---

## Batch Execution from CLI

```bash
# Create output directories
mkdir -p results/bau results/green results/conservative results/aggressive

# Run all four scenarios
esfex run -c scenario_bau.yaml -o results/bau/ --years 25 -v
esfex run -c scenario_green.yaml -o results/green/ --years 25 -v
esfex run -c scenario_conservative.yaml -o results/conservative/ --years 25 -v
esfex run -c scenario_aggressive.yaml -o results/aggressive/ --years 25 -v
```

For parallel execution, use background processes:

```bash
esfex run -c scenario_bau.yaml -o results/bau/ --years 25 &
esfex run -c scenario_green.yaml -o results/green/ --years 25 &
esfex run -c scenario_conservative.yaml -o results/conservative/ --years 25 &
esfex run -c scenario_aggressive.yaml -o results/aggressive/ --years 25 &
wait
echo "All scenarios complete"
```

---

## Result Comparison

```python
import h5py
import numpy as np

scenarios = {
    "BAU": "results/bau/results_island.h5",
    "Green": "results/green/results_island.h5",
    "Conservative": "results/conservative/results_island.h5",
    "Aggressive": "results/aggressive/results_island.h5",
}

print(f"{'Scenario':<15} {'Total NPV ($M)':>15} {'Final RE (%)':>12} "
      f"{'Total Invest ($M)':>18} {'Emissions (ktCO2)':>18}")
print("-" * 80)

for name, path in scenarios.items():
    try:
        with h5py.File(path, "r") as f:
            summary = f["summary_results"]

            total_cost = summary["total_cost"][:].sum() / 1e6
            re_pen = summary["re_penetration"][-1] * 100 if "re_penetration" in summary else 0

            gen_inv = summary["gen_investment_power"][:].sum() if "gen_investment_power" in summary else 0
            bat_inv = summary["bat_investment_power"][:].sum() if "bat_investment_power" in summary else 0
            total_inv = (gen_inv + bat_inv) / 1e6

            emissions = summary["total_emissions"][:].sum() / 1e3 if "total_emissions" in summary else 0

            print(f"{name:<15} {total_cost:>15,.1f} {re_pen:>12.1f} "
                  f"{total_inv:>18,.1f} {emissions:>18,.1f}")
    except Exception as e:
        print(f"{name:<15} ERROR: {e}")
```

### Expected Output

```
Scenario         Total NPV ($M)  Final RE (%)  Total Invest ($M)  Emissions (ktCO2)
--------------------------------------------------------------------------------
BAU                       285.3         28.5               45.2            12,500.3
Green                     312.7         78.2              185.6             4,200.1
Conservative              268.4         48.7               98.3             8,100.5
Aggressive                425.8         97.1              310.2             1,050.8
```

### Year-by-Year Comparison

```python
import matplotlib.pyplot as plt

fig, axes = plt.subplots(2, 2, figsize=(14, 10))

for name, path in scenarios.items():
    with h5py.File(path, "r") as f:
        summary = f["summary_results"]
        years = np.arange(1, len(summary["total_cost"][:]) + 1)

        # Total cost by year
        axes[0, 0].plot(years, summary["total_cost"][:] / 1e6, label=name)

        # RE penetration
        if "re_penetration" in summary:
            axes[0, 1].plot(years, summary["re_penetration"][:] * 100, label=name)

        # Cumulative investment
        if "gen_investment_power" in summary:
            cum_inv = np.cumsum(summary["gen_investment_power"][:].sum(axis=1)) / 1e6
            axes[1, 0].plot(years, cum_inv, label=name)

        # Annual emissions
        if "total_emissions" in summary:
            axes[1, 1].plot(years, summary["total_emissions"][:] / 1e3, label=name)

axes[0, 0].set_title("Annual System Cost")
axes[0, 0].set_ylabel("Cost ($M)")
axes[0, 1].set_title("RE Penetration")
axes[0, 1].set_ylabel("RE (%)")
axes[1, 0].set_title("Cumulative RE Investment")
axes[1, 0].set_ylabel("Capacity (MW)")
axes[1, 1].set_title("Annual CO2 Emissions")
axes[1, 1].set_ylabel("Emissions (ktCO2)")

for ax in axes.flat:
    ax.set_xlabel("Year")
    ax.legend()
    ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig("scenario_comparison.png", dpi=150)
plt.show()
```

---

## Organizing Scenario Files and Results

```
project/
  base_system.yaml              # Common configuration
  data/
    demand.xlsx                 # Demand data
    solar_profile.csv           # Solar availability
    wind_profile.csv            # Wind availability
  scenarios/
    generate_scenarios.py       # Script to generate scenario files
    scenario_bau.yaml
    scenario_green.yaml
    scenario_conservative.yaml
    scenario_aggressive.yaml
  results/
    bau/
      results_island.h5
    green/
      results_island.h5
    conservative/
      results_island.h5
    aggressive/
      results_island.h5
  analysis/
    compare_scenarios.py        # Comparison script
    scenario_comparison.png     # Output charts
    summary_table.csv           # Summary metrics
```

### Generating Summary CSV

```python
import csv

rows = []
for name, path in scenarios.items():
    with h5py.File(path, "r") as f:
        summary = f["summary_results"]
        rows.append({
            "scenario": name,
            "total_npv_musd": summary["total_cost"][:].sum() / 1e6,
            "final_re_pct": summary["re_penetration"][-1] * 100 if "re_penetration" in summary else 0,
            "total_gen_invest_mw": summary["gen_investment_power"][:].sum() if "gen_investment_power" in summary else 0,
            "total_bat_invest_mw": summary["bat_investment_power"][:].sum() if "bat_investment_power" in summary else 0,
            "total_emissions_ktco2": summary["total_emissions"][:].sum() / 1e3 if "total_emissions" in summary else 0,
            "max_load_shed_mw": summary["loss_load"][:].max() if "loss_load" in summary else 0,
        })

with open("analysis/summary_table.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
```

---

## Advanced: Parameterized Scenarios with Environment Variables

```python
import os
import yaml
import subprocess
import copy

# Load base config
with open("base_system.yaml") as f:
    base_config = yaml.safe_load(f)

# Read parameters from environment or defaults
re_target = float(os.environ.get("ESFEX_RE_TARGET", "0.60"))
demand_growth = float(os.environ.get("ESFEX_DEMAND_GROWTH", "0.02"))
carbon_price = float(os.environ.get("ESFEX_CARBON_PRICE", "10.0"))
output_dir = os.environ.get("ESFEX_OUTPUT_DIR", "results/default")

# Modify config
config = copy.deepcopy(base_config)
config["systems"]["island"]["target_re_penetration"] = re_target
config["systems"]["island"]["demand_growth"] = demand_growth
config["systems"]["island"]["penalties"]["co2_cost"] = carbon_price

# Write temporary config
tmp_config = f"/tmp/esfex_config_{os.getpid()}.yaml"
with open(tmp_config, "w") as f:
    yaml.safe_dump(config, f, default_flow_style=False)

# Run simulation
subprocess.run([
    "esfex", "run",
    "-c", tmp_config,
    "-o", output_dir,
    "--years", "25",
    "-v",
], check=True)

# Clean up
os.unlink(tmp_config)
```

Usage:

```bash
ESFEX_RE_TARGET=0.80 ESFEX_CARBON_PRICE=50 ESFEX_OUTPUT_DIR=results/green \
  python run_parameterized.py
```

---

## Key Takeaways

1. **Multiple levers**: Demand growth, RE targets, CO2 budgets, discount rates, and investment limits all interact to determine the optimal energy mix. No single parameter tells the whole story.
2. **Scenario comparison**: Running multiple configs and comparing results is the simplest way to explore the solution space. It requires no special configuration beyond separate YAML files.
3. **Budget constraints**: Investment caps can significantly delay the RE transition. A $200M/year budget may prevent achieving 80% RE even if it is cost-optimal without the constraint.
4. **Discount rate impact**: Moving from 5% to 12% discount rate can shift the optimal solution from solar-dominated to diesel-dominated, because RE's advantage lies in low lifetime costs that are discounted away at high rates.
5. **Stochastic hedging**: For uncertain parameters, use the stochastic framework (see the [Stochastic Planning tutorial](stochastic.md)) rather than running separate deterministic scenarios. Stochastic programming produces a single robust plan; custom scenarios produce multiple incompatible plans.
6. **Automation**: Use Python scripts to generate scenario files and batch scripts to run them in parallel. This is essential for studies with more than 4-5 scenarios.
7. **Structured comparison**: Always produce a summary table and year-by-year charts. Decision-makers need both aggregate metrics (total NPV, final RE%) and trajectory information (how costs and RE evolve over time).
