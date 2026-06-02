# Multi-Node Network

## Prerequisites

- Completed the [Single-Node Tutorial](single-system.md) or familiarity with basic ESFEX configuration
- ESFEX installed: `pip install esfex`
- Python 3.10+ with `h5py`, `numpy`, and `matplotlib` for results analysis

---

## Scenario

A 3-node island system with distinct resource zones:

- **Node 0 (Capital)**: High demand center (peak ~150 MW), existing diesel and some solar, limited space for new RE
- **Node 1 (Coast)**: Moderate demand (~60 MW peak), excellent wind resources, port area
- **Node 2 (Interior)**: Low demand (~40 MW peak), best solar irradiance, abundant land for solar farms

Transmission lines connect the nodes with limited capacity. The optimizer must decide where to invest in generation and whether to expand transmission to deliver remote renewable energy to load centers.

---

## Step 1: Complete Configuration

Create `multi_node_system.yaml`:

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
    demand_path: island_demand_3node.xlsx
    demand_scale: 1.0
    demand_growth: 0.02

    nodes:
      adjacency_matrix:
        - [0, 100, 0]        # Node 0 <-> Node 1: 100 MW
        - [100, 0, 50]       # Node 1 <-> Node 2: 50 MW
        - [0, 50, 0]         # Node 0 <-> Node 2: no direct link
      coordinates:
        - [-76.80, 18.00]    # Capital
        - [-77.50, 18.20]    # Coast
        - [-76.20, 17.80]    # Interior
      names: ["Capital", "Coast", "Interior"]
      reserve_static: [5.0, 2.0, 1.0]
      reserve_dynamic: [3.0, 1.0, 1.0]
      reserve_duration: [2, 2, 2]
      losses: [0.02, 0.02, 0.02]
      transference_invest_cost:
        - [0, 500000, 0]         # Cost to expand Node 0-1 corridor
        - [500000, 0, 600000]    # Cost to expand Node 1-2 corridor
        - [0, 600000, 0]
      invest_max_transfer: 200.0

    dc_power_flow:
      enabled: true
      base_impedance: 100.0
      max_angle_diff_deg: 30.0
      slack_bus: 0

    transmission_lines_geo:
      - line_id: line_0
        from_node: 0
        to_node: 1
        capacity_mw: 100.0
        reactance_pu: 0.05
        resistance_pu: 0.01
        length_km: 150.0
        voltage_kv: 220.0
        num_circuits: 1

      - line_id: line_1
        from_node: 1
        to_node: 2
        capacity_mw: 50.0
        reactance_pu: 0.08
        resistance_pu: 0.015
        length_km: 200.0
        voltage_kv: 110.0
        num_circuits: 1

    generators:
      solar_pv:
        name: Solar PV
        type: Renewable
        fuel: Solar
        rated_power: [20.0, 10.0, 30.0]         # More solar at Interior
        min_power: [0.0, 0.0, 0.0]
        invest_cost: [750000, 750000, 700000]     # Slightly cheaper inland
        invest_max_power: [200, 100, 300]          # More room inland
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
        initial_age: [3, 2, 5]
        degradation_rate: [0.005, 0.005, 0.005]
        inertia: [0.0, 0.0, 0.0]
        Availability: solar_availability.csv

      wind:
        name: Wind
        type: Renewable
        fuel: Wind
        rated_power: [0.0, 30.0, 0.0]            # Wind only at Coast
        min_power: [0.0, 0.0, 0.0]
        invest_cost: [1200000, 1100000, 1300000]
        invest_max_power: [50, 200, 50]            # Major wind potential at Coast
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
        rated_power: [200.0, 50.0, 30.0]          # Most diesel at Capital
        min_power: [0.3, 0.3, 0.3]
        invest_cost: [500000, 500000, 500000]
        invest_max_power: [0.0, 0.0, 0.0]
        fuel_cost: [85.0, 85.0, 85.0]
        fixed_cost: [3.0, 3.0, 3.0]
        maintenance_cost: [5.0, 5.0, 5.0]
        start_up_cost: [5000.0, 5000.0, 5000.0]
        decommissioning_cost: [100000, 100000, 100000]
        ramp_up: [0.5, 0.5, 0.5]
        ramp_down: [0.5, 0.5, 0.5]
        min_up_time: [4, 4, 4]
        min_down_time: [2, 2, 2]
        eff_at_rated: [0.40, 0.40, 0.40]
        eff_at_min: [0.30, 0.30, 0.30]
        life_time: [30, 30, 30]
        initial_age: [15, 10, 12]
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
      annual_limit: 500000.0

    target_re_penetration: 0.80
    initial_re_penetration: 0.0
    max_curtailment_ratio: 0.05
    discount_rate: 0.08
    MAX_ANNUAL_SYSTEM_COST: 500000000.0
```

### Network Topology

The adjacency matrix defines existing transmission capacity between nodes:

```
         Capital  Coast  Interior
Capital  [  0,     100,    0   ]
Coast    [ 100,     0,    50   ]
Interior [  0,      50,    0   ]
```

- **Capital — Coast**: 100 MW bidirectional capacity (line_0)
- **Coast — Interior**: 50 MW bidirectional capacity (line_1)
- **Capital — Interior**: No direct connection (power must flow through Coast)

Wind energy from Coast can reach Capital directly (100 MW limit) or Interior (50 MW limit). Solar energy from Interior must pass through Coast to reach Capital, competing for the 50 MW Coast-Interior corridor.

### DC Power Flow Parameters

The DC power flow model [**[1]**](../reference/bibliography.md#ref1) enforces Kirchhoff's Voltage Law (KVL); power flows are determined by electrical impedance, not just capacity limits:

| Parameter | line_0 | line_1 | Notes |
|-----------|--------|--------|-------|
| `reactance_pu` | 0.05 | 0.08 | Lower reactance = more power flow |
| `resistance_pu` | 0.01 | 0.015 | Causes transmission losses |
| `length_km` | 150 | 200 | Longer line = higher impedance |
| `voltage_kv` | 220 | 110 | Higher voltage = lower losses |

### Per-Node Arrays

In a multi-node system, every generator/battery parameter becomes an array with one entry per node. For example, `rated_power: [20.0, 10.0, 30.0]` means 20 MW solar at Node 0, 10 MW at Node 1, and 30 MW at Node 2. Use `0.0` where a technology is not present at a node.

---

## Step 2: Prepare Input Data

### Multi-Node Demand File

Create `island_demand_3node.xlsx` with 8,760 rows and 3 columns (one per node):

| Hour | Node 0 (Capital) | Node 1 (Coast) | Node 2 (Interior) |
|------|-----------------|----------------|-------------------|
| 1 | 95.0 | 38.0 | 25.0 |
| 2 | 88.0 | 35.0 | 22.0 |
| ... | ... | ... | ... |
| 14 | 148.0 | 58.0 | 40.0 |
| ... | ... | ... | ... |
| 8760 | 100.0 | 40.0 | 27.0 |

The Capital node carries approximately 60% of total demand, the Coast ~24%, and the Interior ~16%.

### Availability Files

The same availability files are applied to all nodes for each generator type. For example, `solar_availability.csv` is shared across all 3 nodes. If solar irradiance differs significantly by location, you can create separate availability files and assign them to separate generator entries (e.g., `solar_capital`, `solar_interior` each with their own `Availability` file).

---

## Step 3: Run the Simulation

```bash
esfex validate -c multi_node_system.yaml
esfex run -c multi_node_system.yaml --years 10 -v
```

The multi-node simulation takes longer because the optimizer considers transmission flows and spatial generation-demand balance. Expect 30-90 minutes depending on hardware.

---

## Step 4: Interpreting Network Results

### Power Flows

```python
import h5py
import numpy as np

with h5py.File("results/output.h5", "r") as f:
    flow = f["detailed_results/island/year_005/power_flow"]
    for key in flow.keys():
        data = flow[key][:]
        print(f"Line {key}: "
              f"avg={data.mean():.1f} MW, "
              f"max={data.max():.1f} MW, "
              f"min={data.min():.1f} MW")
```

Positive values indicate flow in the defined direction (from_node to to_node); negative values indicate reverse flow. For example, on line_0 (Capital-Coast), positive flow means power flows from Capital to Coast, and negative means Coast exports to Capital.

Expected results by year 5:

| Line | Direction | Avg Flow | Peak Flow | Interpretation |
|------|-----------|----------|-----------|----------------|
| line_0 | Coast -> Capital | -45 MW | -98 MW | Wind exports from Coast |
| line_1 | Interior -> Coast | 25 MW | 48 MW | Solar exports from Interior |

### Transmission Congestion Analysis

Congestion occurs when flow reaches line capacity. Congested hours indicate bottlenecks that may justify transmission expansion.

```python
import numpy as np

with h5py.File("results/output.h5", "r") as f:
    # Line 0: Capital-Coast (100 MW capacity)
    flow_0_1 = f["detailed_results/island/year_005/power_flow/(0, 1)"][:]
    capacity_01 = 100.0
    congested_01 = np.sum(np.abs(flow_0_1) >= capacity_01 * 0.99)
    utilization_01 = np.mean(np.abs(flow_0_1)) / capacity_01

    # Line 1: Coast-Interior (50 MW capacity)
    flow_1_2 = f["detailed_results/island/year_005/power_flow/(1, 2)"][:]
    capacity_12 = 50.0
    congested_12 = np.sum(np.abs(flow_1_2) >= capacity_12 * 0.99)
    utilization_12 = np.mean(np.abs(flow_1_2)) / capacity_12

    print(f"Line 0-1 (Capital-Coast):")
    print(f"  Congested hours: {congested_01} / {len(flow_0_1)} "
          f"({congested_01/len(flow_0_1):.1%})")
    print(f"  Average utilization: {utilization_01:.1%}")
    print(f"\nLine 1-2 (Coast-Interior):")
    print(f"  Congested hours: {congested_12} / {len(flow_1_2)} "
          f"({congested_12/len(flow_1_2):.1%})")
    print(f"  Average utilization: {utilization_12:.1%}")
```

Congestion interpretation:

| Congestion Level | Hours/Year | Meaning |
|-----------------|------------|---------|
| Low | < 200 | Line capacity is adequate |
| Moderate | 200-1000 | Potential for cost savings with expansion |
| High | > 1000 | Strong economic case for transmission investment |

### Transmission Investment

The optimizer expands transmission corridors when the congestion cost (lost opportunity to deliver cheap RE) exceeds the expansion cost.

```python
with h5py.File("results/output.h5", "r") as f:
    if "summary_results/transfer_investment" in f:
        transfer_inv = f["summary_results/transfer_investment"]
        for key in transfer_inv.keys():
            val = transfer_inv[key][:]
            print(f"Corridor {key}: {val:.1f} MW expansion")
```

In this scenario, the Coast-Interior corridor (50 MW, $600,000/MW) is likely expanded first because Interior has 300 MW of solar investment potential but only 50 MW of export capacity. The Capital-Coast corridor (100 MW, $500,000/MW) may also be expanded if wind investment at Coast is large.

### Locational Marginal Prices

With multi-node systems, electricity prices vary by location. Each node's price reflects the marginal cost of serving one additional MW of demand there.

```python
with h5py.File("results/output.h5", "r") as f:
    prices = f["detailed_results/island/year_005/prices"][:]
    for n, name in enumerate(["Capital", "Coast", "Interior"]):
        avg_price = prices[n].mean()
        peak_price = prices[n].max()
        off_peak = np.percentile(prices[n], 10)
        print(f"{name}: avg=${avg_price:.2f}/MWh, "
              f"peak=${peak_price:.2f}/MWh, "
              f"off-peak=${off_peak:.2f}/MWh")
```

Expected price patterns:

| Node | Avg Price | Explanation |
|------|-----------|-------------|
| Capital | $80-120/MWh | High demand, relies on diesel + imports |
| Coast | $40-70/MWh | Local wind generation keeps prices low |
| Interior | $30-60/MWh | Abundant solar, but may spike without export capacity |

**Price differences between nodes indicate transmission congestion value.** If Capital prices are consistently $30/MWh higher than Coast during wind hours, the congestion rent on the Capital-Coast line is approximately $30/MWh times the flow, representing the economic value of expanding that corridor.

### Visualizing Power Flows

```python
import matplotlib.pyplot as plt
import numpy as np

with h5py.File("results/output.h5", "r") as f:
    flow_01 = f["detailed_results/island/year_005/power_flow/(0, 1)"][:]
    flow_12 = f["detailed_results/island/year_005/power_flow/(1, 2)"][:]

# Plot one week
hours = np.arange(168)
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

ax1.plot(hours, flow_01[:168], "b-", linewidth=1)
ax1.axhline(y=100, color="r", linestyle="--", label="Capacity (+)")
ax1.axhline(y=-100, color="r", linestyle="--", label="Capacity (-)")
ax1.set_ylabel("Flow (MW)")
ax1.set_title("Line 0-1: Capital - Coast")
ax1.legend()

ax2.plot(hours, flow_12[:168], "g-", linewidth=1)
ax2.axhline(y=50, color="r", linestyle="--", label="Capacity (+)")
ax2.axhline(y=-50, color="r", linestyle="--", label="Capacity (-)")
ax2.set_xlabel("Hour")
ax2.set_ylabel("Flow (MW)")
ax2.set_title("Line 1-2: Coast - Interior")
ax2.legend()

plt.tight_layout()
plt.savefig("transmission_flows.png", dpi=150)
```

### Per-Node Generation Analysis

```python
with h5py.File("results/output.h5", "r") as f:
    gen = f["detailed_results/island/year_010/gen_output"][:]
    node_names = ["Capital", "Coast", "Interior"]
    gen_names = ["Solar", "Wind", "Diesel"]

    for n, node_name in enumerate(node_names):
        print(f"\n{node_name}:")
        for g, gen_name in enumerate(gen_names):
            energy = gen[g, n, :].sum()
            if energy > 0:
                print(f"  {gen_name}: {energy:.0f} MWh")
```

---

## Key Takeaways

1. **Spatial optimization**: The model co-optimizes generation investment and transmission expansion. Cheap remote renewables are only valuable if transmission can deliver them to load centers.
2. **Resource-load mismatch**: Wind at Coast and solar at Interior are far from the Capital load center. This geographic separation drives the need for transmission planning alongside generation planning.
3. **Transmission investment signals**: Congestion hours and locational price differences are the primary indicators of transmission investment value. The optimizer balances expansion costs against congestion savings.
4. **DC power flow (KVL)** [**[1]**](../reference/bibliography.md#ref1): Unlike a simple transport model, the DC power flow ensures physically consistent flows that respect Kirchhoff's laws [**[41]**](../reference/bibliography.md#ref41). Power distributes across parallel paths according to impedance, not just capacity.
5. **Locational prices**: Price differences between nodes directly reflect the marginal value of transmission expansion. Persistent price gaps signal that the network is constraining the use of cheaper generation.
6. **Cascading flows**: Without a direct Capital-Interior link, solar power from Interior must transit through Coast. This means the Coast-Interior and Capital-Coast lines share the burden, and expanding one may shift congestion to the other.

---

## Next Steps

- [Multi-System](multi-system.md) — connect multiple independent power systems
- [Sensitivity Analysis](sensitivity-analysis.md) — analyze sensitivity to transmission costs
- [Configuration Reference](../reference/config-reference.md) — all transmission parameters
