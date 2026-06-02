# Stochastic Planning

## Two-Stage Stochastic Programming

Energy infrastructure investments last 15-30 years. Over that horizon, fuel prices, demand growth, technology costs, and climate conditions are all uncertain. A deterministic plan optimized for a single "best guess" future may perform poorly if conditions differ from expectations [**[9]**](../reference/bibliography.md#ref9).

**Two-stage stochastic programming** [**[10]**](../reference/bibliography.md#ref10) addresses this by considering multiple plausible futures simultaneously:

- **First stage (here-and-now)**: Investment decisions — which generators, batteries, and transmission lines to build — are made once and must be the same across all scenarios. These represent irreversible infrastructure commitments.
- **Second stage (wait-and-see)**: Operational dispatch decisions — how to run generators and charge/discharge batteries — can adapt to whatever future actually occurs.

The optimizer minimizes **expected cost** across all scenarios, weighted by probability. The result is a robust investment plan that hedges against multiple futures.

---

## Scenario Design

Scenarios should represent distinct, plausible futures that span the range of uncertainty.

### Axis-Based Scenarios

Choose 2-3 key uncertainties and create scenarios at their extremes:

| Scenario | Demand Growth | Fuel Price | RE Cost |
|----------|--------------|------------|---------|
| High Demand, Expensive Fuel | High | High | Base |
| Base Case | Base | Base | Base |
| Green Transition | Base | Base | Low |
| Stagnation | Low | Low | Base |

### Probability Assignment

Probabilities reflect the decision-maker's belief about each future:

| Scenario | Probability | Rationale |
|----------|------------|-----------|
| High Demand | 30% | Strong economic growth, urbanization |
| Base Case | 40% | Most likely trajectory |
| Green Transition | 20% | Technology breakthroughs, strong policy |
| Stagnation | 10% | Economic downturn, delayed investment |

Probabilities must sum to 1.0.

---

## Configuration

### Step 1: Enable Stochastic Mode

```yaml
master_problem:
  stochastic: true
  representative_days_per_year: 5
  min_day_separation: 7
```

When `stochastic: true`, the master problem creates separate operational variables for each scenario while sharing investment variables across all scenarios.

### Step 2: Define Scenarios

Scenarios use multipliers that modify base-case parameters:

```yaml
systems:
  island:
    # ... standard system config (nodes, generators, batteries, etc.) ...

    stochastic_scenarios:
      high_demand:
        probability: 0.3
        multipliers:
          demand_multiplier: 1.2            # 20% higher demand
          demand_growth_multiplier: 1.5     # 50% faster growth
          fuel_cost_multiplier: 1.1         # 10% more expensive fuel

      base_case:
        probability: 0.4
        multipliers:
          demand_multiplier: 1.0
          fuel_cost_multiplier: 1.0

      green_transition:
        probability: 0.2
        multipliers:
          investment_cost_multiplier: 0.8   # 20% cheaper RE investment
          battery_cost_multiplier: 0.7      # 30% cheaper batteries
          renewable_availability_multiplier: 1.1  # 10% better RE resources

      stagnation:
        probability: 0.1
        multipliers:
          demand_multiplier: 0.9            # 10% lower demand
          demand_growth_multiplier: 0.6     # Much slower growth
          fuel_cost_multiplier: 0.8         # Cheaper fuel
```

### Available Multipliers

Each multiplier scales the corresponding base-case parameter (1.0 = no change).

| Multiplier | Default | What It Scales |
|-----------|---------|----------------|
| `demand_multiplier` | 1.0 | All electrical demand values (D_n,t) |
| `demand_growth_multiplier` | 1.0 | Annual demand growth rate (compounds over years) |
| `fuel_cost_multiplier` | 1.0 | All generator fuel costs ($/MWh) |
| `renewable_availability_multiplier` | 1.0 | RE capacity factors (solar irradiance, wind speed) |
| `investment_cost_multiplier` | 1.0 | Generator investment costs ($/MW) |
| `battery_cost_multiplier` | 1.0 | Battery investment costs ($/MW and $/MWh) |
| `co2_cost_multiplier` | 1.0 | CO2 emission penalty ($/tCO2) |
| `discount_rate_multiplier` | 1.0 | Discount rate for NPV calculation |
| `ev_adoption_multiplier` | 1.0 | EV fleet growth rate |
| `technology_improvement_multiplier` | 1.0 | Generator efficiency improvements |

### How Multipliers Work

```
parameter_s = parameter_base * multiplier_s
```

For example, if the base fuel cost is $85/MWh and `fuel_cost_multiplier = 1.3`, the effective fuel cost in that scenario is $85 * 1.3 = $110.5/MWh.

`demand_growth_multiplier` compounds: base growth 2%/year with multiplier 1.5 yields 3%/year effective. Over 10 years, ~34% total increase vs. ~22% at base growth.

---

## Complete YAML Example

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
  time_limit: 7200            # Allow more time for stochastic solve
  gap: 0.01

master_problem:
  stochastic: true
  representative_days_per_year: 5
  min_day_separation: 7

meta_network:
  systems: [island]

systems:
  island:
    name: island
    demand_path: demand.xlsx
    demand_scale: 1.0
    demand_growth: 0.02
    discount_rate: 0.05
    target_re_penetration: 0.80
    max_curtailment_ratio: 0.05
    MAX_ANNUAL_SYSTEM_COST: 500000000.0

    nodes:
      adjacency_matrix:
        - [0, 100]
        - [100, 0]
      coordinates:
        - [-82.38, 23.13]
        - [-81.95, 22.40]
      names: ["Node_A", "Node_B"]

    generators:
      diesel:
        name: Diesel
        type: Non-renewable
        fuel: Diesel
        rated_power: [50.0, 30.0]
        min_power: [0.3, 0.3]
        invest_cost: [0, 0]
        invest_max_power: [0, 0]
        fuel_cost: [85.0, 85.0]
        fixed_cost: [5.0, 5.0]
        maintenance_cost: [3.0, 3.0]
        eff_at_rated: [0.38, 0.38]
        eff_at_min: [0.30, 0.30]
        life_time: [30, 30]
        initial_age: [10, 10]

    technologies:
      solar_pv:
        name: Solar PV
        type: Renewable
        fuel: Solar
        invest_cost: [700000, 700000]
        invest_max_power: [200, 100]
        Availability: solar_profile.csv
        eff_at_rated: [1.0, 1.0]
        degradation_rate: [0.005, 0.005]
        lifetime: 25

      wind:
        name: Wind Onshore
        type: Renewable
        fuel: Wind
        invest_cost: [1200000, 1200000]
        invest_max_power: [100, 50]
        Availability: wind_profile.csv
        eff_at_rated: [1.0, 1.0]
        degradation_rate: [0.002, 0.002]
        lifetime: 20

    battery_technologies:
      li_ion:
        name: Li-Ion
        invest_cost_power: [180000, 180000]
        invest_cost_energy: [120000, 120000]
        invest_max_power: [100, 50]
        invest_max_capacity: [400, 200]
        min_duration_hours: 2.0
        max_duration_hours: 8.0
        efficiency_charge: [0.95, 0.95]
        efficiency_discharge: [0.95, 0.95]
        lifetime: 15

    penalties:
      loss_of_load: 10000000.0
      curtailment: 100.0
      max_curtailment_ratio: 0.05
      fre_penetration_loss: 100.0

    stochastic_scenarios:
      high_demand:
        probability: 0.3
        multipliers:
          demand_multiplier: 1.2
          demand_growth_multiplier: 1.5
          fuel_cost_multiplier: 1.1

      base_case:
        probability: 0.5
        multipliers:
          demand_multiplier: 1.0
          fuel_cost_multiplier: 1.0

      green_transition:
        probability: 0.2
        multipliers:
          investment_cost_multiplier: 0.8
          battery_cost_multiplier: 0.7
          renewable_availability_multiplier: 1.1
```

---

## Running the Stochastic Optimization

```bash
esfex run -c stochastic_config.yaml --years 10 -v
```

Expected log:

```
ESFEX - Power System Optimization
Configuration: stochastic_config.yaml
Mode: development
Solver: highs

Configuration Summary
+-------------------+------------------+
| Setting           | Value            |
+-------------------+------------------+
| Simulation Mode   | development      |
| Solver            | highs            |
| Systems           | island           |
| Stochastic        | 3 scenarios      |
+-------------------+------------------+

Solving stochastic master problem...
  Scenario 'high_demand' (prob=0.30): building operational constraints...
  Scenario 'base_case' (prob=0.50): building operational constraints...
  Scenario 'green_transition' (prob=0.20): building operational constraints...
  Total variables: 245,000  Total constraints: 312,000
  Solver: HiGHS (4 threads, gap=1%)
  Optimal objective: $142,500,000 (expected cost)
  Solve time: 145.3 seconds
```

The stochastic master problem creates separate operational variables per scenario while sharing investment variables, increasing solve time.

---

## Interpreting Results

### Robust Investment Decisions

A single set of investment decisions applies across all scenarios:

```python
import h5py
import numpy as np

with h5py.File("results/results_island.h5", "r") as f:
    summary = f["summary_results"]

    # Investment decisions (same for all scenarios)
    if "gen_investment_power" in summary:
        gen_inv = summary["gen_investment_power"][:]
        print("Generator investments (MW) by year:")
        for y, inv in enumerate(gen_inv):
            if inv.sum() > 0:
                print(f"  Year {y+1}: {inv}")

    if "bat_investment_power" in summary:
        bat_inv = summary["bat_investment_power"][:]
        print("\nBattery investments (MW) by year:")
        for y, inv in enumerate(bat_inv):
            if inv.sum() > 0:
                print(f"  Year {y+1}: {inv}")
```

### Per-Scenario Operational Outcomes

Operational outcomes (dispatch, costs, emissions) differ by scenario:

```python
with h5py.File("results/results_island.h5", "r") as f:
    # Expected (probability-weighted) total cost
    total_cost = f["summary_results/total_cost"][:]
    print(f"Expected total cost (NPV): ${total_cost.sum():,.0f}")

    # Per-scenario costs (if available)
    if "stochastic_results" in f:
        stoch = f["stochastic_results"]
        for scenario_name in stoch.keys():
            scenario_cost = stoch[scenario_name]["total_cost"][:]
            prob = stoch[scenario_name].attrs.get("probability", "?")
            print(f"  {scenario_name} (prob={prob}): ${scenario_cost.sum():,.0f}")
```

### EVPI: Expected Value of Perfect Information

EVPI measures the value of knowing which scenario will occur:

```
EVPI = E[cost_stochastic] - E[cost_with_perfect_info]
```

Compute EVPI by running the deterministic optimization per scenario, weighted by probabilities:

```bash
# Run deterministic for each scenario (modify YAML manually or via script)
esfex run -c scenario_high_demand.yaml -o results/det_high/
esfex run -c scenario_base_case.yaml -o results/det_base/
esfex run -c scenario_green.yaml -o results/det_green/
```

```python
import h5py

# Deterministic costs under perfect information
det_costs = {
    "high_demand": ("results/det_high/results_island.h5", 0.3),
    "base_case": ("results/det_base/results_island.h5", 0.5),
    "green_transition": ("results/det_green/results_island.h5", 0.2),
}

expected_perfect = 0.0
for name, (path, prob) in det_costs.items():
    with h5py.File(path, "r") as f:
        cost = f["summary_results/total_cost"][:].sum()
        expected_perfect += prob * cost
        print(f"  {name}: ${cost:,.0f}")

print(f"\nExpected cost with perfect info: ${expected_perfect:,.0f}")

# Stochastic cost
with h5py.File("results/results_island.h5", "r") as f:
    stoch_cost = f["summary_results/total_cost"][:].sum()

evpi = stoch_cost - expected_perfect
print(f"Stochastic expected cost:        ${stoch_cost:,.0f}")
print(f"EVPI:                            ${evpi:,.0f}")
print(f"EVPI as % of stochastic cost:    {evpi/stoch_cost:.1%}")
```

Small EVPI (< 2-3%): deterministic solution is nearly as good. Large EVPI (> 5-10%): uncertainty significantly affects optimal investment and stochastic programming provides real value.

### VSS: Value of the Stochastic Solution

VSS measures the benefit of stochastic programming vs. optimizing for the expected (average) scenario:

```
VSS = E[cost_deterministic_expected] - E[cost_stochastic]
```

Run the deterministic optimization with mean parameter values, then evaluate that plan under each scenario:

```python
# Deterministic plan cost evaluated across scenarios
# (requires running deterministic plan in each scenario's conditions)
vss = deterministic_expected_cost - stoch_cost
print(f"VSS: ${vss:,.0f}")
print(f"VSS as % of deterministic cost: {vss/deterministic_expected_cost:.1%}")
```

A positive VSS means the stochastic solution saves money. Typical VSS: 1-15% depending on uncertainty level and system flexibility.

---

## Scenario Comparison and Hedging

The stochastic solution typically exhibits hedging behavior:

- **More diversified investments**: Instead of betting entirely on solar (optimal under green transition) or diesel (optimal under high demand), the stochastic solution invests in a mix.
- **Earlier storage investment**: Batteries provide operational flexibility that is valuable across all scenarios.
- **Moderate RE targets**: Rather than aggressive RE deployment (optimal under green transition) or minimal RE (optimal under stagnation), the stochastic solution takes a middle path.

### Example Comparison Table

| Metric | Deterministic (Base) | Stochastic | Difference |
|--------|---------------------|------------|------------|
| Solar PV investment (MW) | 180 | 150 | -30 MW |
| Wind investment (MW) | 0 | 40 | +40 MW |
| Battery investment (MW) | 60 | 80 | +20 MW |
| Total cost (expected, $M) | 148 | 142 | -$6M |
| RE penetration (year 10) | 72% | 68% | -4% |
| Max load shedding (any scenario) | 12 MW | 3 MW | -9 MW |

The stochastic solution costs less in expectation with much lower worst-case load shedding, at the expense of slightly lower RE penetration in the base case.

---

## Computational Considerations

### Problem Size Scaling

| Component | Deterministic | 3 Scenarios | 5 Scenarios |
|-----------|--------------|-------------|-------------|
| Investment variables | V1 | V1 | V1 |
| Operational variables | V2 | 3 x V2 | 5 x V2 |
| Total constraints | C | C + 3 x C2 | C + 5 x C2 |
| Approximate solve time | T | ~3T | ~5T |

Problem size grows linearly with scenarios (operational variables replicated, investment variables shared).

### Practical Guidelines

| Scenarios | Solve Time | Recommended Use |
|-----------|-----------|----------------|
| 2-3 | 2-5x deterministic | Standard planning studies |
| 5-7 | 5-10x deterministic | Detailed uncertainty analysis |
| 10+ | 10-20x deterministic | Only with powerful hardware or scenario reduction |

### Tips for Large Problems

1. **Reduce representative days**: Use 3-5 representative days per year instead of 7-10. This is the biggest lever for reducing problem size.
2. **Coarser temporal resolution**: Set `resolution_hours: 2` or `resolution_hours: 6` to reduce hourly variables.
3. **Fewer years**: Start with 5-year horizons for testing, then extend to 10-25 years.
4. **Solver tuning**: Use Gurobi or CPLEX with barrier method for large stochastic problems. HiGHS is adequate for 2-3 scenarios but may be slow for more.
5. **Scenario reduction**: Start with many candidate scenarios and use k-means or similar techniques to select 3-5 representative scenarios that capture the key uncertainties.

---

## When to Use Stochastic vs. Deterministic

| Situation | Recommendation |
|-----------|---------------|
| Quick screening, single future | Deterministic |
| Moderate uncertainty, key decisions | Stochastic (3 scenarios) |
| High uncertainty, large investments | Stochastic (5+ scenarios) |
| Policy analysis, multiple alternatives | Deterministic + MGA/SPORES |
| Publication, comprehensive study | Stochastic + EVPI/VSS analysis |

### Decision Flowchart

1. Run deterministic base case first
2. Run sensitivity analysis [**[11]**](../reference/bibliography.md#ref11) to identify key uncertainties
3. If 2+ parameters have high Sobol indices (ST > 0.15), use stochastic programming
4. Design 2-3 scenarios covering the most uncertain parameters
5. Compare stochastic vs. deterministic investment plans
6. If EVPI > 5%, the stochastic solution provides significant value

---

## Key Takeaways

1. **Robust investments**: Stochastic programming produces investment plans that perform well across multiple futures, avoiding the risk of over-committing to a single scenario.
2. **Expected cost**: The objective minimizes probability-weighted total cost. The result may not be optimal for any single scenario but is best on average.
3. **Hedging behavior**: Stochastic solutions typically invest in more diverse technology portfolios and earlier storage deployment.
4. **EVPI and VSS**: These metrics quantify the value of better information (EVPI) and better modeling (VSS). They justify the additional computational effort of stochastic programming.
5. **Computational cost**: Scales linearly with the number of scenarios. Start with 2-3 scenarios; increase only if EVPI analysis suggests significant value.
6. **Scenario design**: Focus scenarios on the most impactful uncertainties (identified via sensitivity analysis). Scenario probabilities encode the planner's beliefs about the future.

!!! warning "Validation Advisory"
    The stochastic framework is structurally complete but should be validated carefully for production studies. Start with 2-3 scenarios and verify that per-scenario operational results are consistent with expectations before scaling up.
