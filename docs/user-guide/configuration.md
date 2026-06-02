# Configuration Guide

## Top-Level Structure

YAML configuration files are validated by Pydantic v2. The configuration drives the two-stage decomposition [**[25]**](../reference/bibliography.md#ref25): capacity expansion via the master problem [**[24]**](../reference/bibliography.md#ref24) followed by operational dispatch using a rolling horizon approach. A complete file has the following top-level keys:

```yaml
# Simulation settings
simulation_mode: development          # "development" or "unit_commitment"
unit_commitment_hours: 24             # Hours for UC window (UC mode only)
date_start: "01/01/2025 00:00"       # Simulation start date (DD/MM/YYYY HH:MM)

# Module configurations
temporal: { ... }                     # Time resolution settings
solver: { ... }                       # Solver settings
n1_security: { ... }                  # N-1 contingency settings
master_problem: { ... }              # Capacity expansion settings
enable_primary_energy: true           # Enable fuel supply chain

# Network definition
meta_network:
  systems: [system_a, system_b]       # List of system names
  systems_links: { ... }             # Inter-system connections

# System definitions
systems:
  system_a: { ... }                   # Full system configuration
  system_b: { ... }
```

### Simulation Modes

| Mode | Description | Model Type | Use Case |
|------|-------------|------------|----------|
| `development` | Economic dispatch (ED) | Pure LP | Long-term planning, capacity expansion |
| `unit_commitment` | Unit commitment (UC) | MIP | Short-term scheduling with binary start/stop |

`development` mode formulates a pure LP with continuous generator output -- recommended for multi-year capacity expansion studies. `unit_commitment` mode introduces binary commitment variables, producing a MIP that models startup/shutdown costs and minimum up/down time constraints.


---


## Temporal Configuration

```yaml
temporal:
  resolution_hours: 1                 # Time step (hours): 1, 2, 3, 4, 6
  use_rolling_horizon: true           # Enable rolling horizon dispatch
  rolling_horizon_hours: 48           # Window size (hours)
  overlap_hours: 6                    # Overlap between windows

  # Sub-model resolutions (upscaling for faster solve)
  investment_resolution: 0            # Master problem (0 = full resolution)
  primary_energy_resolution: 24       # Fuel model (hours per period)
  battery_soc_resolution: 0           # SOC tracking (0 = full)
  ev_resolution: 0                    # EV model (0 = full)
  reserve_resolution: 0              # Reserve model (0 = full)
```

The rolling horizon splits each year into overlapping windows. Only the non-overlapping portion contributes to the final solution; the overlap ensures continuity of storage SOC and ramp trajectories.

Sub-model resolutions allow individual components to operate at coarser time steps. For example, `primary_energy_resolution: 24` uses daily time blocks for the fuel supply chain, reducing variables while retaining hourly generator dispatch. A value of `0` uses the same resolution as the main model.


---


## Solver Configuration

```yaml
solver:
  name: highs                         # highs, cbc, glpk, gurobi, cplex, scip, xpress
  threads: 4                          # Number of solver threads
  time_limit: 3600                    # Max solve time (seconds)
  gap: 0.01                           # MIP optimality gap (1%)
  verbose: false                      # Show solver output
  options: {}                         # Solver-specific key-value pairs
```

The `options` dictionary passes solver-specific parameters directly to the JuMP optimizer:

```yaml
solver:
  name: highs
  threads: 4
  options:
    presolve: "on"
    solver_method: "ipm"
    simplex_scale_strategy: "max_equilibration"
    primal_feasibility_tolerance: 1.0e-7
    dual_feasibility_tolerance: 1.0e-7
```

See the [Solver Guide](solver-guide.md) for solver-specific options and tuning.


---


## N-1 Security

N-1 contingency analysis ensures the system remains secure if any single transmission line or generator fails.

```yaml
n1_security:
  enabled: false                      # Enable N-1 constraints
  transmission_enabled: false         # Transmission contingency
  generation_enabled: false           # Generation contingency
  transmission_reserve_factor: 0.8    # Line loading limit under N-1
  generation_reserve_factor: 1.0      # Generation margin
```

When `transmission_enabled` is `true`, the model identifies the critical N-1 element at each time step and enforces that remaining lines can carry the redistributed flow. The `transmission_reserve_factor` limits line loading to 80% of thermal capacity for contingency headroom.


---


## Master Problem

```yaml
master_problem:
  stochastic: false                   # Enable stochastic programming
  representative_days_per_year: 5     # Days for operational validation
  min_day_separation: 7              # Min days between representatives

  # MGA / SPORES: Near-optimal alternative exploration
  mga:
    enabled: false                    # Master toggle
    method: mga                       # "mga" (HSJ loop) | "spores" (per-objective sweep)
    # MGA-only knob (ignored under SPORES):
    num_alternatives: 10              # K alternatives for the HSJ loop
    # SPORES-only knob (must be empty under MGA):
    objectives: []                    # e.g. [min_total_build, max_regional_equity]
    # Shared knobs:
    slack_fraction: 0.05              # Cost slack (0.05 = 5% above optimal)
    investment_threshold: 0.1         # MW threshold for HSJ frequency scoring
```

ESFEX offers two near-optimal exploration methods. Both share the cost-slack envelope $Z \leq (1+\varepsilon) C^*$ but differ in how alternatives are produced:

- **`method: mga`** (default) — runs the classical Hop-Skip-Jump (HSJ) loop `num_alternatives` times. Each iteration maximises a single diversity objective that penalises investment variables seen in previous alternatives.
- **`method: spores`** — solves *one alternative per declared objective* (see [SporesObjective](../api/config-schema.md#sporesobjective)). The alternative count equals `len(objectives)`; `num_alternatives` is ignored. Example:

```yaml
master_problem:
  mga:
    enabled: true
    method: spores
    objectives:
      - min_total_build      # smallest near-optimal portfolio
      - max_tech_equity      # technology-diversified plan
      - max_regional_equity  # spatially-spread plan
      - evolutionary_dist    # maximally different from cost-optimal
    slack_fraction: 0.05
```

Results are exported under `/mga/` in the HDF5 file with `/mga.attrs["method"]` and a per-alternative `objective` tag (see [HDF5 Output Schema § MGA Results](../reference/hdf5-output-schema.md#mgaspores-results)). Alternative 0 (cost-optimal) is used for operational dispatch.


---


## System Configuration

### Demand

```yaml
systems:
  my_system:
    name: my_system
    demand_path: demand.xlsx          # Path to demand data file
    demand_scale: 1.0                 # Demand multiplier
    demand_growth: 0.02               # 2% annual growth rate
```

Demand growth is applied multiplicatively: year `y` demand equals `D_base * (1 + demand_growth)^(y-1)`. See [Demand Data](demand-data.md) for file format details.

### Nodes

```yaml
    nodes:
      adjacency_matrix:               # N x N connectivity (0 or capacity MW)
        - [0, 100, 0]
        - [100, 0, 50]
        - [0, 50, 0]
      coordinates:                    # [longitude, latitude] per node
        - [-82.38, 23.13]
        - [-81.95, 22.40]
        - [-80.45, 22.07]
      names: ["Havana", "Cienfuegos", "Camaguey"]
```

The adjacency matrix is symmetric: entry `(i, j)` gives transmission capacity in MW. A value of `0` means no direct connection. Node coordinates are used for distance calculations (zone interconnection costs, GUI map display).

### Generators

Each generator is defined as a named entry with per-node arrays:

```yaml
    generators:
      solar_pv:
        name: Solar PV
        type: Renewable               # "Renewable" or "Non-renewable"
        fuel: Solar
        rated_power: [50.0, 30.0, 20.0]   # MW per node
        min_power: [0.0, 0.0, 0.0]        # Fraction of rated
        invest_cost: [800000, 800000, 800000]  # $/MW
        invest_max_power: [500, 300, 200]      # MW max investment
        fuel_cost: [0.0, 0.0, 0.0]            # $/MWh
        fixed_cost: [5.0, 5.0, 5.0]           # $/MWh (fixed O&M)
        maintenance_cost: [2.0, 2.0, 2.0]     # $/MWh (variable O&M)
        start_up_cost: [0.0, 0.0, 0.0]        # $/start
        decommissioning_cost: [0, 0, 0]        # $/MW
        ramp_up: [1.0, 1.0, 1.0]              # Fraction/hour
        ramp_down: [1.0, 1.0, 1.0]
        min_up_time: [0, 0, 0]                 # Hours (UC mode only)
        min_down_time: [0, 0, 0]
        eff_at_rated: [1.0, 1.0, 1.0]         # Efficiency at rated power
        eff_at_min: [1.0, 1.0, 1.0]           # Efficiency at min power
        life_time: [25, 25, 25]                # Years
        initial_age: [0, 0, 0]                 # Years
        degradation_rate: [0.005, 0.005, 0.005] # Annual capacity degradation
        inertia: [0.0, 0.0, 0.0]              # MVA*s
        Availability: solar_profile.csv        # Availability file path
```

Every per-node parameter must have exactly `num_nodes` entries; a mismatch causes a validation error.

#### Generator Age and Retirement

Generators are retired when age exceeds lifetime:

- **Existing units**: `age = initial_age + (year_idx - 1)`
- **Invested units**: `age = year_idx - investment_year`

Setting `initial_age: [20, 20, 20]` with `life_time: [25, 25, 25]` means the generator retires after 5 simulation years.

### Cost Curves and Bidding Curves

Piecewise cost curves replace the flat `fuel_cost` parameter. Four curve types are available:

```yaml
    generators:
      gas_turbine:
        name: Gas Turbine
        type: Non-renewable
        fuel: Natural Gas
        # ... other fields ...
        fuel_cost_curve:
          - curve_type: stepwise       # Node 0: stepwise blocks
            blocks:
              - { fraction: 0.4, price: 45.0 }   # First 40% at $45/MWh
              - { fraction: 0.3, price: 55.0 }   # Next 30% at $55/MWh
              - { fraction: 0.3, price: 75.0 }   # Last 30% at $75/MWh
          - curve_type: linear         # Node 1: linear ramp
            price_at_zero: 40.0        # $/MWh at minimum output
            price_at_max: 80.0         # $/MWh at rated power
          - curve_type: flat           # Node 2: constant price
            flat_price: 50.0           # $/MWh at all output levels
```

Supported `curve_type` values:

| Curve Type | Parameters | Description |
|------------|-----------|-------------|
| `flat` | `flat_price` | Constant marginal cost at all output levels |
| `linear` | `price_at_zero`, `price_at_max` | Linear interpolation from min to max output |
| `stepwise` | `blocks` (list of `{fraction, price}`) | Piecewise constant blocks; fractions must sum to 1.0 |
| `exponential` | `base_price`, `scale_factor` | `price(P) = base_price * exp(scale_factor * P / P_max)` |

`fuel_cost_curve` overrides the flat `fuel_cost` array. Each list entry corresponds to one node. All curves are internally normalized to stepwise blocks for the optimizer.

### Technologies (Investment Candidates)

Technologies define investment options for new capacity. Unlike generators (existing physical units), these are candidates evaluated by the master problem.

```yaml
    technologies:
      solar_pv_new:
        name: Solar PV (New)
        type: Renewable
        fuel: Solar
        invest_cost: [700000, 700000, 700000]    # $/MW per node
        invest_max_power: [500, 300, 200]         # MW max per node
        Availability: solar_profile.csv
        eff_at_rated: [1.0, 1.0, 1.0]
        degradation_rate: [0.005, 0.005, 0.005]
        lifetime: 25
        fuel_cost: [0.0, 0.0, 0.0]
        fixed_cost: [5.0, 5.0, 5.0]
        maintenance_cost: [2.0, 2.0, 2.0]

      wind_onshore:
        name: Wind Onshore
        type: Renewable
        fuel: Wind
        invest_cost: [1200000, 1200000, 1200000]
        invest_max_power: [200, 100, 100]
        Availability: wind_profile.csv
        eff_at_rated: [1.0, 1.0, 1.0]
        degradation_rate: [0.002, 0.002, 0.002]
        lifetime: 20
        fuel_cost: [0.0, 0.0, 0.0]
        fuel_cost_curve:                          # Optional: per-node bidding curves
          - curve_type: flat
            flat_price: 0.0
          - curve_type: flat
            flat_price: 0.0
          - curve_type: flat
            flat_price: 0.0
```

Selected technologies produce virtual generator instances that participate in operational dispatch.

### Battery Technologies

```yaml
    battery_technologies:
      li_ion_4h:
        name: Li-Ion 4h
        invest_cost_power: [180000, 180000, 180000]    # $/MW per node
        invest_cost_energy: [120000, 120000, 120000]   # $/MWh per node
        invest_max_power: [200, 100, 100]              # MW max per node
        invest_max_capacity: [800, 400, 400]           # MWh max per node
        min_duration_hours: 2.0                        # Min E/P ratio (hours)
        max_duration_hours: 8.0                        # Max E/P ratio (hours)
        efficiency_charge: [0.95, 0.95, 0.95]
        efficiency_discharge: [0.95, 0.95, 0.95]
        degradation_rate: [0.01, 0.01, 0.01]
        lifetime: 15
        soc_initial: [0.5, 0.5, 0.5]
        max_DoD: [0.9, 0.9, 0.9]
        maintenance_cost: [1.0, 1.0, 1.0]
        decommissioning_cost: [5000, 5000, 5000]
```

### Batteries (Existing)

```yaml
    batteries:
      li_ion:
        name: Li-Ion Storage
        capacity: [0.0, 0.0, 0.0]            # MWh per node
        max_charge_power: [0.0, 0.0, 0.0]    # MW
        max_discharge_power: [0.0, 0.0, 0.0] # MW
        charge_efficiency: [0.95, 0.95, 0.95]
        discharge_efficiency: [0.95, 0.95, 0.95]
        soc_min: [0.10, 0.10, 0.10]          # Fraction
        soc_max: [0.95, 0.95, 0.95]
        soc_initial: [0.50, 0.50, 0.50]
        self_discharge: [0.0001, 0.0001, 0.0001]  # Per hour
        invest_cost_power: [200000, 200000, 200000]    # $/MW
        invest_cost_capacity: [150000, 150000, 150000]  # $/MWh
        invest_max_power: [200, 100, 100]      # MW
        invest_max_capacity: [800, 400, 400]   # MWh
        min_duration_hours: 2.0                # Min E/P ratio
        max_duration_hours: 8.0                # Max E/P ratio
        life_time: [15, 15, 15]
        maintenance_cost: [1.0, 1.0, 1.0]     # $/MWh
        spillage: false                        # Allow energy spillage
```

Battery SOC is subject to a cyclic constraint: `SOC(t_last) == SOC(t_initial)` at the end of each operational day, preventing batteries from acting as free energy sources.

### Penalties

Penalties are costs for violating soft constraints. They guide the optimizer toward feasible solutions and provide shadow prices for constraint relaxation.

```yaml
    penalties:
      loss_of_load: 10000000.0            # $/MW not supplied (VOLL)
      curtailment: 100.0                  # $/MWh curtailed energy
      loss_of_reserve_static: 100.0       # $/MW static reserve deficit
      loss_of_reserve_dynamic: 100.0      # $/MW dynamic reserve deficit
      loss_of_inertia: 200.0              # $/MW-s inertia deficit
      transfer_margin: 100.0              # $/MW transfer margin violation
      fre_penetration_loss: 100.0         # $/MWh RE shortfall
      co2_cost: 10.0                      # $/tCO2 carbon price
      co2_budget_violation: 500.0         # $/tCO2 over budget
      max_curtailment_ratio: 0.05         # Max curtailment as fraction of RE gen
      ev_loss: 10.0                       # $/MWh EV demand not met
      loss_of_fuel_supply: 100.0          # $/MW-eq fuel deficit
      transport_congestion: 100.0         # $/MW congestion penalty
      storage_violation: 100.0            # $/MW storage constraint violation
      non_electric_demand_loss: 100.0     # $/unit fuel demand unmet
      soc_violation: 1000000.0            # $/MWh SOC limit violation
      delay_retirement_per_mw: 50000.0    # $/MW retirement delay penalty
      rooftop_curtailment: 5.0            # $/MWh rooftop solar curtailed
```

`loss_of_load` (VOLL) must be significantly higher than any generation cost so the optimizer prefers expensive dispatch over load shedding. Typical VOLL values: $1,000-$50,000/MWh depending on jurisdiction.

`max_curtailment_ratio` limits RE curtailment to a fraction of total RE generation (e.g., `0.05` = 5% maximum). Enforced as a constraint, not a cost.

### Targets and Constraints

```yaml
    co2_budget:
      enabled: true
      annual_budget: 1000000.0            # tonnes CO2/year

    target_re_penetration: 0.80           # 80% RE target
    initial_re_penetration: 0.0           # Starting RE fraction (auto-calculated if 0)
    min_annual_increment: 0.0             # Min RE increase per year
    max_annual_increment: 1.0             # Max RE increase per year
    max_curtailment_ratio: 0.05           # 5% curtailment limit

    # Economic parameters
    discount_rate: 0.05                   # 5% discount rate
    demand_growth: 0.02                   # 2% annual demand growth
    MAX_ANNUAL_SYSTEM_COST: 1000000000.0  # Annual investment budget ($/year)
```

When `initial_re_penetration` is `0.0`, ESFEX auto-calculates it from existing renewable capacities. The RE target is enforced as a master problem constraint, with `fre_penetration_loss` as the shortfall penalty.

### DC Power Flow

```yaml
    dc_power_flow:
      enabled: true
      base_impedance: 100.0               # Ohms
      max_angle_diff_deg: 30.0            # Max voltage angle difference (degrees)

    transmission_lines_geo:               # Per-line data
      - line_id: line_0
        from_node: 0
        to_node: 1
        capacity_mw: 100.0
        reactance_pu: 0.05
        resistance_pu: 0.01
        length_km: 200.0
        voltage_kv: 220.0
      - line_id: line_1
        from_node: 1
        to_node: 2
        capacity_mw: 50.0
        reactance_pu: 0.08
        resistance_pu: 0.015
        length_km: 150.0
        voltage_kv: 110.0
```

With DC power flow enabled, transmission uses linearized power flow equations: `P_ij = (theta_i - theta_j) / x_ij`. The `max_angle_diff_deg` constraint limits angle differences between connected nodes.

With DC power flow disabled, a transport model applies: flows are limited only by line capacity without angle constraints.

### EV Configuration

```yaml
    ev_categories:
      sedan:
        battery_capacity_kwh: 60.0
        max_charge_power_kw: 11.0
        max_discharge_power_kw: 7.0
        charge_efficiency: 0.95
        discharge_efficiency: 0.92
        min_soc: 0.20
        max_soc: 0.90

    ev_quantity:
      sedan: [1000, 500, 300]             # Per node

    EV_initial_soc: [0.5, 0.5, 0.5]      # Per node

    base_patterns:
      commuter: [0.1, 0.1, ..., 0.8, 0.9]    # 24 hourly availability values
```

EV charging demand uses an S-curve growth model. When EV optimization is enabled, the optimizer decides charging/V2G schedules; when disabled, EV demand is added to `total_demand`.

### Sectoral Demand

```yaml
    electric_demand:
      residential:
        criticality: 0.7       # 0 = fully flexible, 1 = critical
        flexibility: 0.3
      industrial:
        criticality: 0.9
        flexibility: 0.1
      commercial:
        criticality: 0.5
        flexibility: 0.5

    sector_distribution:
      0:                        # Node 0
        residential: 0.40       # 40% residential
        industrial: 0.35
        commercial: 0.25
      1:                        # Node 1
        residential: 0.50
        industrial: 0.30
        commercial: 0.20
```

Fractions per node must sum to 1.0. Higher `criticality` sectors are shed last.


---


## Multi-System Configuration

```yaml
meta_network:
  systems: [cuba_main, isla_juventud]
  dynamic_transfer_pricing: false

  systems_links:
    - systems: [cuba_main, isla_juventud]
      from_nodes: [2]                     # Node in system A
      to_nodes: [0]                       # Node in system B
      capacity: [50.0]                    # MW existing
      invest_cost: [500000.0]             # $/MW for expansion
      invest_max: [200.0]                 # MW max expansion
```

Each system is solved independently for operational dispatch but shares investment decisions through the master problem.


---


## External System Files

```yaml
# main_config.yaml
meta_network:
  systems: [mainland, island]

systems:
  mainland: mainland_system.yaml      # Reference to external file
  island: island_system.yaml
```

Paths are resolved relative to the main configuration file.


---


## Validation

Pydantic v2 validates all configuration at load time:

- **Type checking**: All fields must match expected types
- **Range validation**: Values must be within acceptable ranges (e.g., efficiencies in [0, 1])
- **Cross-field validation**: Array lengths must match `num_nodes`
- **File existence**: Referenced data files (demand, availability) are checked
- **Enum validation**: Fields like `type` must be one of the allowed values

Common validation errors:

| Error | Cause | Fix |
|-------|-------|-----|
| `list length must match num_nodes` | Per-node array wrong size | Add/remove entries to match node count |
| `field required` | Missing required field | Add the field to YAML |
| `value is not a valid float` | String where number expected | Remove quotes from numeric values |
| `Input should be 'Renewable' or 'Non-renewable'` | Invalid generator type | Use exact case: `Renewable` or `Non-renewable` |
| `ensure this value is greater than or equal to 0` | Negative value where positive required | Check for typos in numeric values |

### Validating Before Running

```bash
esfex validate -c my_system.yaml
```

Performs all checks without starting the optimization.


---


## Complete Example

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
  representative_days_per_year: 5

enable_primary_energy: false

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
    MAX_ANNUAL_SYSTEM_COST: 1000000000.0

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
        start_up_cost: [500, 500]
        decommissioning_cost: [10000, 10000]
        ramp_up: [0.5, 0.5]
        ramp_down: [0.5, 0.5]
        min_up: [2, 2]
        min_down: [2, 2]
        eff_at_rated: [0.38, 0.38]
        eff_at_min: [0.30, 0.30]
        life_time: [30, 30]
        initial_age: [10, 10]
        degradation_rate: [0.01, 0.01]
        inertia: [5.0, 5.0]

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
        degradation_rate: [0.01, 0.01]
        lifetime: 15

    penalties:
      loss_of_load: 10000000.0
      fre_penetration_loss: 100.0
      curtailment: 100.0
      max_curtailment_ratio: 0.05
```

See the [Config Reference](../reference/config-reference.md) for a complete field listing.
