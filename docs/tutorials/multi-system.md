# Multi-System Interconnection

## Prerequisites

- Completed the [Single-Node Tutorial](single-system.md) and [Multi-Node Tutorial](multi-node.md)
- ESFEX installed: `pip install esfex`
- Python 3.10+ with `h5py` and `numpy` for results analysis

---

## Scenario

Two island systems connected by a submarine cable:

- **System A (Main Island)**: 3 nodes, ~500 MW peak demand, diverse generation mix (diesel, solar, wind). Relatively developed grid with transmission between nodes.
- **System B (Small Island)**: 1 node, ~50 MW peak demand, limited generation (diesel + small solar). High electricity costs due to small scale and fuel dependency.

The submarine cable allows power transfer between Node 2 of the Main Island and Node 0 of the Small Island.

---

## Step 1: Complete Configuration

Create `multi_system.yaml`:

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

# --- Meta-Network: defines which systems exist and how they connect ---
meta_network:
  systems:
    - main_island
    - small_island
  dynamic_transfer_pricing: false

  systems_links:
    submarine_cable:
      systems: [main_island, small_island]
      from_nodes: [2]           # Node 2 of main_island
      to_nodes: [0]             # Node 0 of small_island
      capacity: [30.0]          # MW existing submarine cable capacity
      invest_cost: [800000.0]   # $/MW for cable expansion
      invest_max: [100.0]       # MW maximum cable expansion
      losses: [0.03]            # 3% transmission losses

# --- System A: Main Island (3 nodes) ---
systems:
  main_island:
    name: main_island
    demand_path: main_island_demand.xlsx
    demand_scale: 1.0
    demand_growth: 0.02

    nodes:
      adjacency_matrix:
        - [0, 100, 50]
        - [100, 0, 80]
        - [50, 80, 0]
      coordinates:
        - [-76.80, 18.00]     # Node 0: Capital
        - [-77.50, 18.20]     # Node 1: West Coast
        - [-76.20, 17.80]     # Node 2: East (cable landing)
      names: ["Capital", "West Coast", "East"]
      reserve_static: [5.0, 2.0, 2.0]
      reserve_dynamic: [3.0, 1.0, 1.0]
      reserve_duration: [2, 2, 2]
      losses: [0.02, 0.02, 0.02]

    generators:
      solar_pv:
        name: Solar PV
        type: Renewable
        fuel: Solar
        rated_power: [30.0, 20.0, 40.0]
        min_power: [0.0, 0.0, 0.0]
        invest_cost: [750000, 750000, 700000]
        invest_max_power: [200, 150, 250]
        fuel_cost: [0.0, 0.0, 0.0]
        fixed_cost: [5.0, 5.0, 5.0]
        maintenance_cost: [2.0, 2.0, 2.0]
        start_up_cost: [0.0, 0.0, 0.0]
        decommissioning_cost: [0, 0, 0]
        ramp_up: [1.0, 1.0, 1.0]
        ramp_down: [1.0, 1.0, 1.0]
        min_up_time: [0, 0, 0]
        min_down_time: [0, 0, 0]
        eff_at_rated: [1.0, 1.0, 1.0]
        eff_at_min: [1.0, 1.0, 1.0]
        life_time: [25, 25, 25]
        initial_age: [3, 2, 4]
        degradation_rate: [0.005, 0.005, 0.005]
        inertia: [0.0, 0.0, 0.0]
        Availability: solar_availability.csv

      wind:
        name: Wind
        type: Renewable
        fuel: Wind
        rated_power: [0.0, 40.0, 0.0]
        min_power: [0.0, 0.0, 0.0]
        invest_cost: [1200000, 1100000, 1300000]
        invest_max_power: [100, 300, 50]
        fuel_cost: [0.0, 0.0, 0.0]
        fixed_cost: [8.0, 8.0, 8.0]
        maintenance_cost: [5.0, 5.0, 5.0]
        start_up_cost: [0.0, 0.0, 0.0]
        decommissioning_cost: [0, 0, 0]
        ramp_up: [1.0, 1.0, 1.0]
        ramp_down: [1.0, 1.0, 1.0]
        min_up_time: [0, 0, 0]
        min_down_time: [0, 0, 0]
        eff_at_rated: [1.0, 1.0, 1.0]
        eff_at_min: [1.0, 1.0, 1.0]
        life_time: [20, 20, 20]
        initial_age: [0, 0, 0]
        degradation_rate: [0.005, 0.005, 0.005]
        inertia: [0.0, 0.0, 0.0]
        Availability: wind_availability.csv

      diesel:
        name: Diesel
        type: Non-renewable
        fuel: Diesel
        rated_power: [200.0, 80.0, 50.0]
        min_power: [0.3, 0.3, 0.3]
        invest_cost: [500000, 500000, 500000]
        invest_max_power: [0.0, 0.0, 0.0]
        fuel_cost: [80.0, 80.0, 80.0]
        fixed_cost: [3.0, 3.0, 3.0]
        maintenance_cost: [5.0, 5.0, 5.0]
        start_up_cost: [5000, 5000, 5000]
        decommissioning_cost: [100000, 100000, 100000]
        ramp_up: [0.5, 0.5, 0.5]
        ramp_down: [0.5, 0.5, 0.5]
        min_up_time: [4, 4, 4]
        min_down_time: [2, 2, 2]
        eff_at_rated: [0.40, 0.40, 0.40]
        eff_at_min: [0.30, 0.30, 0.30]
        life_time: [30, 30, 30]
        initial_age: [12, 8, 10]
        degradation_rate: [0.01, 0.01, 0.01]
        inertia: [5.0, 5.0, 5.0]

    batteries:
      li_ion:
        name: Li-Ion Battery
        capacity: [0.0, 0.0, 0.0]
        max_charge_power: [0.0, 0.0, 0.0]
        max_discharge_power: [0.0, 0.0, 0.0]
        charge_efficiency: [0.95, 0.95, 0.95]
        discharge_efficiency: [0.95, 0.95, 0.95]
        soc_min: [0.10, 0.10, 0.10]
        soc_max: [0.95, 0.95, 0.95]
        soc_initial: [0.50, 0.50, 0.50]
        self_discharge: [0.0001, 0.0001, 0.0001]
        invest_cost_power: [200000, 200000, 200000]
        invest_cost_capacity: [150000, 150000, 150000]
        invest_max_power: [100, 100, 100]
        invest_max_capacity: [400, 400, 400]
        min_duration_hours: 2.0
        max_duration_hours: 6.0
        life_time: [15, 15, 15]
        maintenance_cost: [1.0, 1.0, 1.0]
        spillage: false
        degradation_rate: [0.02, 0.02, 0.02]

    penalties:
      LOSS_DEMAND_TRHESHOLD: 10000.0
      curtailment_penalty: 50.0
      loss_reserve_static_penalty: 500.0
      fre_penalty: 600.0

    co2_budget:
      annual_limit: 800000.0

    target_re_penetration: 0.80
    initial_re_penetration: 0.0
    max_curtailment_ratio: 0.05
    discount_rate: 0.08
    MAX_ANNUAL_SYSTEM_COST: 500000000.0

  # --- System B: Small Island (1 node) ---
  small_island:
    name: small_island
    demand_path: small_island_demand.xlsx
    demand_scale: 1.0
    demand_growth: 0.03      # Higher growth on small island

    nodes:
      adjacency_matrix: [[0]]
      coordinates: [[-75.50, 17.50]]
      names: ["Small Island"]

    generators:
      solar_pv:
        name: Solar PV
        type: Renewable
        fuel: Solar
        rated_power: [10.0]
        min_power: [0.0]
        invest_cost: [800000.0]
        invest_max_power: [80.0]
        fuel_cost: [0.0]
        fixed_cost: [6.0]
        maintenance_cost: [3.0]
        start_up_cost: [0.0]
        decommissioning_cost: [0]
        ramp_up: [1.0]
        ramp_down: [1.0]
        min_up_time: [0]
        min_down_time: [0]
        eff_at_rated: [1.0]
        eff_at_min: [1.0]
        life_time: [25]
        initial_age: [2]
        degradation_rate: [0.005]
        inertia: [0.0]
        Availability: solar_availability.csv

      diesel:
        name: Diesel
        type: Non-renewable
        fuel: Diesel
        rated_power: [40.0]
        min_power: [0.3]
        invest_cost: [500000.0]
        invest_max_power: [0.0]
        fuel_cost: [120.0]        # Higher fuel cost (shipping premium)
        fixed_cost: [5.0]
        maintenance_cost: [8.0]
        start_up_cost: [3000.0]
        decommissioning_cost: [80000]
        ramp_up: [0.5]
        ramp_down: [0.5]
        min_up_time: [3]
        min_down_time: [2]
        eff_at_rated: [0.35]
        eff_at_min: [0.25]
        life_time: [25]
        initial_age: [18]
        degradation_rate: [0.015]
        inertia: [4.0]

    batteries:
      li_ion:
        name: Li-Ion Battery
        capacity: [0.0]
        max_charge_power: [0.0]
        max_discharge_power: [0.0]
        charge_efficiency: [0.93]
        discharge_efficiency: [0.93]
        soc_min: [0.15]
        soc_max: [0.90]
        soc_initial: [0.50]
        self_discharge: [0.0002]
        invest_cost_power: [250000.0]
        invest_cost_capacity: [180000.0]
        invest_max_power: [50.0]
        invest_max_capacity: [200.0]
        min_duration_hours: 2.0
        max_duration_hours: 4.0
        life_time: [12]
        maintenance_cost: [2.0]
        spillage: false
        degradation_rate: [0.025]

    penalties:
      LOSS_DEMAND_TRHESHOLD: 10000.0
      curtailment_penalty: 50.0
      loss_reserve_static_penalty: 500.0
      fre_penalty: 600.0

    co2_budget:
      annual_limit: 100000.0

    target_re_penetration: 0.70
    initial_re_penetration: 0.0
    max_curtailment_ratio: 0.08
    discount_rate: 0.10         # Higher risk premium for small island
    MAX_ANNUAL_SYSTEM_COST: 100000000.0
```

### Key Design Decisions

**Why separate systems instead of one big multi-node system?**

Multi-system modeling is appropriate when:

- The systems have separate regulatory environments, budgets, or RE targets
- They are connected by a limited, discrete interconnector (submarine cable)
- Each system has its own demand file, discount rate, and planning constraints
- You want to analyze the value of interconnection vs. autarky

**Inter-system link parameters:**

| Parameter | Value | Meaning |
|-----------|-------|---------|
| `capacity` | 30 MW | Existing submarine cable capacity |
| `invest_cost` | $800,000/MW | High cost due to submarine installation |
| `invest_max` | 100 MW | Maximum cable expansion |
| `losses` | 3% | Transmission losses over the submarine cable |

---

## Step 2: Prepare Input Data

### Main Island Demand

Create `main_island_demand.xlsx` with 8,760 rows and 3 columns (one per node). Total peak demand should be approximately 500 MW distributed across the three nodes.

### Small Island Demand

Create `small_island_demand.xlsx` with 8,760 rows and 1 column. Peak demand is approximately 50 MW.

### Shared Availability Files

Both systems can use the same `solar_availability.csv` and `wind_availability.csv` if they are geographically close enough to share similar weather patterns.

---

## Step 3: Run the Simulation

```bash
esfex validate -c multi_system.yaml
esfex run -c multi_system.yaml --years 15 -v
```

The multi-system simulation takes longer because the Master Problem creates investment variables for both systems plus the inter-system link. Expect 45-120 minutes.

---

## Step 4: How Multi-System Optimization Works

The optimization proceeds in three layers:

1. **Master Problem**: The multi-system master problem (`create_multi_system_master_problem()`) creates investment variables for each system and adds inter-system link constraints. It sees all years simultaneously and decides:
   - How much generation/storage to invest in each system
   - Whether to expand the submarine cable
   - The optimal timing for each investment

2. **Investment coordination**: The optimizer trades off between:
   - Building local generation on the Small Island (expensive: higher fuel costs, smaller scale)
   - Building excess RE on the Main Island and exporting via the cable (requires cable expansion)
   - A hybrid approach that balances local resilience with export economics

3. **Operational dispatch**: Each system is dispatched independently for each year, with inter-system transfers modeled as fixed import/export schedules determined by the Master Problem.

---

## Step 5: Results Analysis

### Per-System Investments

```python
import h5py
import numpy as np

with h5py.File("results/output.h5", "r") as f:
    for sys_name in ["main_island", "small_island"]:
        print(f"\n=== {sys_name.upper()} ===")
        if f"summary_results/{sys_name}/investments" in f:
            inv = f[f"summary_results/{sys_name}/investments"][:]
            print("Investments (MW per year):")
            print(inv)

        if f"summary_results/{sys_name}/objectives" in f:
            obj = f[f"summary_results/{sys_name}/objectives"][:]
            print(f"Total NPV: ${obj.sum():,.0f}")
```

### Inter-System Transfer Analysis

```python
with h5py.File("results/output.h5", "r") as f:
    if "summary_results/inter_system_transfer" in f:
        transfer = f["summary_results/inter_system_transfer"][:]
        print(f"\nSubmarine cable transfer profile:")
        for yr, val in enumerate(transfer, 1):
            direction = "Main -> Small" if val >= 0 else "Small -> Main"
            print(f"  Year {yr}: {abs(val):.1f} MW avg ({direction})")
```

### Cable Expansion Decisions

```python
with h5py.File("results/output.h5", "r") as f:
    if "summary_results/inter_system_investment" in f:
        cable_inv = f["summary_results/inter_system_investment"][:]
        print(f"\nCable expansion: {cable_inv.sum():.1f} MW total")
        for yr, val in enumerate(cable_inv, 1):
            if val > 0.1:
                print(f"  Year {yr}: +{val:.1f} MW")
```

### Comparative Cost Analysis

```python
with h5py.File("results/output.h5", "r") as f:
    main_obj = f["summary_results/main_island/objectives"][:].sum()
    small_obj = f["summary_results/small_island/objectives"][:].sum()
    cable_cost = 800000 * cable_inv.sum()  # Approximate cable investment cost

    print(f"\nTotal system cost breakdown:")
    print(f"  Main Island NPV:  ${main_obj:,.0f}")
    print(f"  Small Island NPV: ${small_obj:,.0f}")
    print(f"  Cable investment:  ${cable_cost:,.0f}")
    print(f"  Combined total:    ${main_obj + small_obj + cable_cost:,.0f}")
```

### Value of Interconnection

To quantify cable value, compare with an isolated scenario (set `capacity: [0.0]` and `invest_max: [0.0]` in the link).

```python
# After running both scenarios:
# isolated_results/output.h5 (no cable) vs results/output.h5 (with cable)
with h5py.File("isolated_results/output.h5", "r") as f_iso:
    with h5py.File("results/output.h5", "r") as f_linked:
        iso_cost = (f_iso["summary_results/main_island/objectives"][:].sum() +
                    f_iso["summary_results/small_island/objectives"][:].sum())
        linked_cost = (f_linked["summary_results/main_island/objectives"][:].sum() +
                       f_linked["summary_results/small_island/objectives"][:].sum())
        savings = iso_cost - linked_cost
        print(f"Value of interconnection: ${savings:,.0f} "
              f"({savings/iso_cost:.1%} cost reduction)")
```

Expected result: interconnection saves 5-15% of total system cost by:

- Allowing the Small Island to import cheap RE instead of running expensive local diesel ($120/MWh)
- Reducing storage needs on the Small Island (imports provide flexibility)
- Enabling the Main Island to build slightly more RE than needed, exporting the surplus

---

## Key Takeaways

1. **System independence**: Each system maintains its own RE targets, CO2 budgets, and investment constraints. The interconnection provides economic coordination without forcing identical policies.
2. **Inter-system coordination**: The Master Problem jointly optimizes investments across systems. It may invest more RE on the Main Island specifically to export to the Small Island.
3. **Transfer investment**: The optimizer expands the submarine cable when the marginal cost of cable capacity ($800,000/MW) is less than the avoided cost of local generation on the Small Island.
4. **Asymmetric benefit**: The Small Island typically benefits more from the interconnection because its local generation costs are higher. However, the Main Island also benefits from economies of scale in RE investment.
5. **Losses matter**: The 3% cable losses mean that 30 MW exported from the Main Island delivers only 29.1 MW to the Small Island. High losses reduce the value of long-distance interconnection.
6. **Resilience trade-off**: Heavy reliance on the cable makes the Small Island vulnerable to cable outages. The `target_re_penetration` on the Small Island ensures some local generation capacity is maintained.

---

## Next Steps

- [EV Integration](ev-integration.md) — add electric vehicles to either or both systems
- [Stochastic Planning](stochastic.md) — evaluate cable investment under demand uncertainty
- [Custom Scenarios](custom-scenarios.md) — compare different cable capacities and costs
