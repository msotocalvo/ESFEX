# EV Integration

## Scenario

An island power system with growing EV adoption over a 15-year planning horizon. Three vehicle categories — private sedans, commercial delivery vans, and public transit buses — each have distinct charging patterns and V2G participation rates. The optimizer schedules EV charging and determines whether V2G discharge can reduce peak generation needs.

---

## Prerequisites

- A working ESFEX installation with Julia backend configured
- A base system configuration YAML file (e.g., from the Getting Started tutorial)
- Demand and availability profile files for the system

---

## S-Curve Growth Model

ESFEX models EV fleet growth using a logistic (S-curve) function [**[53]**](../reference/bibliography.md#ref53): slow initial uptake, rapid mid-period growth, and eventual saturation.

The fleet size at year `t` is calculated as:

```
fleet(t) = initial_fleet * max_adoption / (1 + exp(-growth_rate * (t - mid_point_year)))
```

Where:

- `initial_fleet`: Number of vehicles at the base year (from `ev_quantity`)
- `max_adoption`: Maximum growth multiplier on the initial fleet (e.g., 30 means up to 30x)
- `growth_rate`: Controls how fast adoption accelerates (higher = steeper curve)
- `mid_point_year`: Year at which growth is fastest (inflection point)
- `mid_point_fraction`: Position of the inflection point within the planning horizon (0.5 = midpoint)

### Growth Rate Examples

| `growth_rate` | Adoption Speed | Typical Use Case |
|---------------|---------------|------------------|
| 0.05 | Very slow | Conservative policy, no subsidies |
| 0.12 | Moderate | Gradual transition with incentives |
| 0.20 | Fast | Aggressive policy, strong subsidies |
| 0.30 | Very fast | Mandated transition, ban on ICE sales |

### Max Adoption Examples

| `max_adoption` | Meaning | Scenario |
|----------------|---------|----------|
| 5 | 5x initial fleet | Low electrification |
| 15 | 15x initial fleet | Moderate electrification |
| 30 | 30x initial fleet | Near-full electrification |
| 50 | 50x initial fleet | Growth beyond current vehicle stock |

---

## Configuration

### Complete YAML Example

```yaml
systems:
  island:
    # ... (nodes, generators, batteries, etc.)

    # ── EV Categories ──────────────────────────────────────────────
    ev_categories:
      sedan:
        battery_capacity_kwh: 60.0
        max_charge_power_kw: 11.0         # Level 2 AC charging
        max_discharge_power_kw: 7.0       # V2G discharge limit
        charging_power: 11.0              # Used for profile generation (kW)
        v2g_power: 7.0                    # V2G power per vehicle (kW)
        v2g_participation: 0.30           # 30% of parked sedans participate in V2G
        charge_efficiency: 0.95
        discharge_efficiency: 0.92
        min_soc: 0.20                     # Never discharge below 20%
        max_soc: 0.90                     # Never charge above 90%
        v2g_compensation: 0.05            # $/kWh compensation to EV owners
        max_adoption: 30.0                # Up to 30x initial fleet
        growth_rate: 0.12                 # Moderate adoption speed
        mid_point_fraction: 0.5           # Inflection at year 7-8 of 15

      commercial_van:
        battery_capacity_kwh: 80.0
        max_charge_power_kw: 22.0         # Level 2 fast AC
        max_discharge_power_kw: 15.0
        charging_power: 22.0
        v2g_power: 15.0
        v2g_participation: 0.15           # Lower V2G -- commercial use priority
        charge_efficiency: 0.94
        discharge_efficiency: 0.91
        min_soc: 0.25                     # Higher minimum for delivery reliability
        max_soc: 0.90
        v2g_compensation: 0.06
        max_adoption: 20.0
        growth_rate: 0.10
        mid_point_fraction: 0.55          # Slightly later adoption than sedans

      electric_bus:
        battery_capacity_kwh: 300.0
        max_charge_power_kw: 50.0         # DC fast charging at depot
        max_discharge_power_kw: 30.0
        charging_power: 50.0
        v2g_power: 30.0
        v2g_participation: 0.50           # High V2G -- centrally managed fleets
        charge_efficiency: 0.93
        discharge_efficiency: 0.90
        min_soc: 0.30                     # Higher reserve for public service
        max_soc: 0.85
        v2g_compensation: 0.08
        max_adoption: 15.0                # Public transit grows more slowly
        growth_rate: 0.08
        mid_point_fraction: 0.45          # Earlier adoption (government-led)

    # ── Fleet Quantities (initial, per node) ───────────────────────
    ev_quantity:
      sedan: [500, 300, 200]              # Nodes 0, 1, 2
      commercial_van: [80, 50, 30]
      electric_bus: [20, 10, 5]

    # ── Initial State of Charge ────────────────────────────────────
    EV_initial_soc: [0.6, 0.6, 0.6]      # 60% SOC at start, per node

    # ── 24-Hour Base Charging Patterns ─────────────────────────────
    base_patterns:
      sedan:
        # Private vehicles: charge after work/overnight
        - [0.05, 0.05, 0.05, 0.05, 0.05, 0.10,   # 00-05: overnight trickle
           0.15, 0.10, 0.05, 0.05, 0.05, 0.05,    # 06-11: most at work
           0.05, 0.05, 0.10, 0.15, 0.20, 0.80,    # 12-17: return home
           0.90, 0.85, 0.70, 0.50, 0.30, 0.10]    # 18-23: evening charge peak

      commercial_van:
        # Commercial: charge overnight at depot, operate during day
        - [0.70, 0.70, 0.65, 0.60, 0.50, 0.20,   # 00-05: depot charging
           0.05, 0.0, 0.0, 0.0, 0.0, 0.0,         # 06-11: on delivery routes
           0.0, 0.0, 0.0, 0.0, 0.05, 0.10,         # 12-17: partial return
           0.20, 0.40, 0.60, 0.70, 0.70, 0.70]     # 18-23: return to depot

      electric_bus:
        # Public transit: charge at night depot, operate dawn-to-dusk
        - [0.80, 0.80, 0.80, 0.70, 0.50, 0.10,   # 00-05: depot charging
           0.0, 0.0, 0.0, 0.0, 0.0, 0.0,          # 06-11: morning routes
           0.0, 0.0, 0.0, 0.0, 0.0, 0.0,           # 12-17: afternoon routes
           0.05, 0.10, 0.30, 0.50, 0.70, 0.80]     # 18-23: return to depot
```

### Pattern Design Guidelines

Base patterns represent the fraction of vehicles plugged in and charging at each hour (0.0 to 1.0):

| Factor | Private Sedans | Commercial Vans | Public Buses |
|--------|---------------|-----------------|--------------|
| Peak charging hours | 18:00-22:00 | 00:00-05:00 | 00:00-05:00 |
| Operating hours | 07:00-17:00 | 06:00-18:00 | 06:00-21:00 |
| Charging location | Home/work | Depot | Depot |
| Pattern variability | High (diverse users) | Medium | Low (scheduled) |

### V2G Configuration Details

V2G [**[51]**](../reference/bibliography.md#ref51) allows EVs to discharge back to the grid during peak demand or high-price periods [**[52]**](../reference/bibliography.md#ref52). Key parameters:

- **`v2g_participation`**: Fraction of parked vehicles willing to provide V2G (0.0 to 1.0). Centrally managed fleets (buses) have higher participation than private vehicles.
- **`v2g_power`**: Maximum discharge power per vehicle in kW. Typically lower than charge power to preserve battery health.
- **`v2g_compensation`**: Payment to vehicle owners per kWh discharged ($/kWh). Must be high enough to offset battery degradation costs.
- **`min_soc`**: Minimum state of charge — the optimizer cannot discharge below this level, ensuring vehicles retain enough range for their next trip.
- **`discharge_efficiency`**: Round-trip losses for V2G (typically 90-92%).

The effective V2G capacity at any hour is:

```
V2G_capacity = num_vehicles * v2g_participation * availability * v2g_power / 1000  [MW]
```

Where `availability` comes from the complement of the charging pattern (vehicles that are parked but not charging can provide V2G).

---

## Running the Simulation

```bash
# Run with verbose output
esfex run -c ev_system.yaml --years 15 -v

# Run with specific output directory
esfex run -c ev_system.yaml --years 15 -o results/ev_study/ -v
```

Expected console output during execution:

```
[INFO] Loading configuration: ev_system.yaml
[INFO] Generating EV profiles for 3 categories, 3 nodes, 15 years
[INFO]   sedan: 1000 initial vehicles, S-curve max_adoption=30.0, growth_rate=0.12
[INFO]   commercial_van: 160 initial vehicles, S-curve max_adoption=20.0, growth_rate=0.10
[INFO]   electric_bus: 35 initial vehicles, S-curve max_adoption=15.0, growth_rate=0.08
[INFO] EV demand added to total demand (optimizer handles V2G scheduling)
[INFO] Solving master problem (year 1/15)...
...
```

---

## Results Analysis

### EV Charging Profiles Over Time

```python
import h5py
import numpy as np

with h5py.File("results/ev_study/output.h5", "r") as f:
    print(f"{'Year':>6} {'Peak Charge (MW)':>18} {'Peak V2G (MW)':>16} "
          f"{'Total Charge (GWh)':>20} {'Total V2G (GWh)':>18}")
    print("-" * 82)

    for yr in [1, 3, 5, 8, 10, 12, 15]:
        grp = f"detailed_results/island/year_{yr:03d}"
        charging = f[f"{grp}/ev_charging"][:]
        v2g = f[f"{grp}/ev_v2g"][:]

        print(f"{yr:>6} {charging.max():>18.1f} {v2g.max():>16.1f} "
              f"{charging.sum() / 1000:>20.1f} {v2g.sum() / 1000:>18.1f}")
```

Expected output (approximate values for a 3-node island):

```
  Year  Peak Charge (MW)   Peak V2G (MW)  Total Charge (GWh)    Total V2G (GWh)
----------------------------------------------------------------------------------
     1               4.2             1.1                12.3               2.8
     3               6.8             1.9                20.1               4.6
     5              14.5             4.2                45.2              10.1
     8              38.2            11.0               118.5              27.3
    10              62.1            18.5               195.0              45.8
    12              78.4            23.1               248.2              58.0
    15              85.6            25.0               271.5              63.2
```

Note the S-curve shape: slow growth in years 1-3, rapid acceleration in years 5-10, and saturation by year 12-15.

### Fleet Growth Impact on System Demand

```python
with h5py.File("results/ev_study/output.h5", "r") as f:
    base = f["demand/island/base_demand"][:]
    ev = f["demand/island/ev_demand"][:]
    total = f["demand/island/total_demand"][:]

    print(f"Base peak demand:  {base.max():.1f} MW")
    print(f"EV peak demand:    {ev.max():.1f} MW")
    print(f"Total peak demand: {total.max():.1f} MW")
    print(f"EV share of peak:  {ev.max() / total.max():.1%}")
    print(f"EV share of energy: {ev.sum() / total.sum():.1%}")
```

### V2G Revenue and Grid Contribution

```python
with h5py.File("results/ev_study/output.h5", "r") as f:
    for yr in [5, 10, 15]:
        grp = f"detailed_results/island/year_{yr:03d}"
        v2g = f[f"{grp}/ev_v2g"][:]
        prices = f[f"{grp}/prices"][:]

        # V2G revenue (price * discharge volume)
        revenue = np.sum(v2g * prices)
        # V2G contribution to peak shaving
        peak_hour = np.argmax(prices)
        v2g_at_peak = v2g[peak_hour] if peak_hour < len(v2g) else 0

        print(f"Year {yr}:")
        print(f"  V2G revenue:           ${revenue:>12,.0f}")
        print(f"  V2G at system peak:    {v2g_at_peak:>8.1f} MW")
        print(f"  Total V2G energy:      {v2g.sum():>8.0f} MWh")
```

### Hourly Charging Profile Visualization

```python
import numpy as np

with h5py.File("results/ev_study/output.h5", "r") as f:
    # Extract a typical day (day 180, summer) for year 10
    grp = "detailed_results/island/year_010"
    charging = f[f"{grp}/ev_charging"][:]

    # Hours 4320 to 4344 = day 180
    day_start = 180 * 24
    day_end = day_start + 24
    day_profile = charging[day_start:day_end]

    print("Hour | Charging (MW) | Bar")
    print("-" * 50)
    for h in range(24):
        bar = "#" * int(day_profile[h] / 2)
        print(f"  {h:02d} | {day_profile[h]:>12.1f} | {bar}")
```

---

## Cost Implications of EV Integration

| Cost Component | Effect of EVs | Direction |
|---------------|---------------|-----------|
| Generation capacity | More peak capacity needed for charging | Increases cost |
| RE investment | More solar/wind to serve EV load | Increases investment |
| Battery storage | V2G reduces need for grid batteries | Decreases investment |
| Fuel cost | Higher demand increases fossil fuel use | Increases cost |
| Curtailment | EVs can absorb excess RE (smart charging) | Decreases waste |
| Grid reinforcement | Higher peak loads stress transmission | Increases cost |

To quantify cost impacts, compare runs with and without EV:

```python
scenarios = {
    "No EV": "results/no_ev/output.h5",
    "With EV": "results/ev_study/output.h5",
}

for name, path in scenarios.items():
    with h5py.File(path, "r") as f:
        cost = f["summary_results/objectives"][:].sum()
        re_pen = f["summary_results/re_penetration"][-1]
        gen_inv = f["summary_results/gen_investment_power"][:].sum()
        bat_inv = f["summary_results/bat_investment_power"][:].sum()

        print(f"\n{name}:")
        print(f"  Total NPV:          ${cost:>14,.0f}")
        print(f"  Final RE penetration: {re_pen:>8.1%}")
        print(f"  Total gen investment: {gen_inv:>8.1f} MW")
        print(f"  Total bat investment: {bat_inv:>8.1f} MW")
```

---

## Practical Tips

1. **Start small**: Test with a single EV category (sedan) before adding commercial and bus categories. This simplifies debugging.

2. **Avoid double-counting EV demand**: When EV optimization is enabled, the optimizer schedules EV charging. Do not manually add `ev_demand` to `total_demand` — the runner handles this automatically.

3. **Pattern sensitivity**: Charging patterns strongly influence peak demand. Run sensitivity analysis on pattern shapes if uncertain about real-world behavior.

4. **V2G economics**: Set `v2g_compensation` above the estimated battery degradation cost (typically 0.03-0.08 $/kWh) to ensure realistic participation rates.

5. **Node distribution**: Distribute EV quantities across nodes proportionally to population or vehicle registration data. Uneven distribution reveals localized grid stress.

6. **Growth rate calibration**: Compare your S-curve parameters against historical EV adoption data from comparable regions. Norway (fast), EU average (moderate), and developing economies (slow) provide useful benchmarks.

7. **Computational note**: EV profiles are generated once at simulation startup and stored in memory. They do not add significant computation time beyond the additional demand they create.

---

## Key Takeaways

1. **S-curve adoption** [**[53]**](../reference/bibliography.md#ref53): Fleet growth starts slow, accelerates in mid-years, then saturates — matching real-world technology diffusion patterns.
2. **Demand impact**: EV charging becomes a significant fraction of total demand by year 10-15, potentially 15-30% of peak load.
3. **V2G value**: Bidirectional charging provides peak shaving and reserve services, partially offsetting the cost of serving EV demand.
4. **Charging patterns**: Off-peak charging (buses at night) is naturally aligned with low-cost hours and can absorb excess renewable generation.
5. **Storage synergy**: EV batteries complement stationary storage, potentially reducing grid-scale battery investment needs by 10-25%.
6. **Category diversity**: Different vehicle types (private, commercial, transit) have complementary charging patterns, smoothing aggregate demand.
