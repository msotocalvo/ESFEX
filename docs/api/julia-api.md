# Julia API

Module: `ESFEX.jl`

## Module Structure

```julia
module ESFEX
    using JuMP, HiGHS, Graphs, LinearAlgebra, SparseArrays, Statistics

    include("types.jl")            # Core type definitions
    include("transmission_dc.jl")  # DC power flow (Kirchhoff formulation)
    include("transmission_ac.jl")  # AC power flow (Newton-Raphson verification)
    include("power_system.jl")     # Operational dispatch / unit commitment
    include("primary_energy.jl")   # Fuel supply chain optimization
    include("master_problem.jl")   # Capacity expansion planning
    include("mga.jl")              # MGA/SPORES near-optimal alternatives
    include("electrolyzer.jl")     # Hydrogen electrolyzer model
end
```

---

## Key Types

### PowerSystemInput

Main input structure for operational dispatch. All vectors are indexed per-bus (or per-node if `num_buses == num_nodes`). Matrices are indexed as `[time, bus]`.

```julia
@kwdef struct PowerSystemInput
    name::String
    year::Int
    base_year::Int
    network::NetworkConfig
    generators::Vector{GeneratorConfig}
    batteries::Vector{BatteryConfig}
    demand::Matrix{Float64}                    # (hours x buses) in MW
    sectoral_demand::Dict{String, Matrix{Float64}}
    temporal::TemporalConfig
    # Penalty coefficients
    loss_of_load_penalty::Float64              # $/MW for unserved energy
    curtailment_penalty::Float64               # $/MW for RE curtailment
    loss_of_reserve_static::Float64            # $/MW for static reserve shortfall
    loss_of_reserve_dynamic::Float64           # $/MW for dynamic reserve shortfall
    co2_cost::Float64                          # $/tonne CO2
    # Targets
    re_penetration_target::Float64             # RE penetration ratio (0-1)
    co2_budget::Float64                        # Annual CO2 budget (tonnes, Inf = unconstrained)
    inertia_limit::Float64                     # Minimum system inertia (GW*s)
    # Solver settings
    mode::String                               # "development", "economic_dispatch", "unit_commitment"
    solver_name::String                        # "highs", "gurobi", etc.
    threads::Int
    time_limit::Float64
    gap::Float64
    verbose::Bool
    # Optional subsystem configurations
    fuel_co2::Dict{String, Float64}
    ev_config::Union{EVConfig, Nothing}
    electrolyzer_config::Union{ElectrolyzerConfig, Nothing}
    # N-1 security
    n1_security_enabled::Bool
    n1_transmission_enabled::Bool
    n1_generation_enabled::Bool
    # ... additional fields (see types.jl for full list)
end
```

### GeneratorConfig

Per-generator configuration. All vectors are indexed per-bus.

```julia
struct GeneratorConfig
    name::String
    type::String                    # "Renewable" or "Non-renewable"
    fuel::String                    # "Sun", "Wind", "Diesel", "Natural Gas", etc.
    reservable::Bool                # Can provide reserve services
    rated_power::Vector{Float64}    # MW per bus
    min_power::Vector{Float64}      # MW per bus (minimum stable output)
    efficiency_rated::Vector{Float64}
    efficiency_min::Vector{Float64}
    ramp_up::Vector{Float64}        # Fraction of rated per timestep
    ramp_down::Vector{Float64}
    min_up_time::Vector{Int}        # Minimum up time (timesteps)
    min_down_time::Vector{Int}      # Minimum down time (timesteps)
    fuel_cost::Vector{Float64}      # $/MWh per bus
    fixed_cost::Vector{Float64}     # $/MW/year per bus
    maintenance_cost::Vector{Float64}
    start_up_cost::Vector{Float64}  # $/start per bus
    invest_cost::Vector{Float64}    # $/MW per bus
    invest_max::Vector{Float64}     # Maximum investment MW per bus
    availability::Matrix{Float64}   # (hours x buses) capacity factor [0,1]
    life_time::Vector{Int}          # Economic lifetime (years) per bus
    initial_age::Vector{Int}        # Current age (years) per bus
    degradation_rate::Vector{Float64}
    decommissioning_cost::Vector{Float64}
    inertia::Vector{Float64}        # Inertia constant H (GW*s/MW) per bus
    frequency_hz::Float64           # Nominal frequency (Hz)
    current_type::String            # "AC" or "DC"
    # Reservoir hydro fields
    reservoir_capacity::Vector{Float64}     # MWh storage capacity
    reservoir_initial_level::Vector{Float64} # Initial level (fraction 0-1)
    reservoir_inflow::Matrix{Float64}       # (hours x buses) inflow in MW
    reservoir_min_level::Vector{Float64}    # Minimum level (fraction)
end
```

### BatteryConfig

```julia
struct BatteryConfig
    name::String
    capacity::Vector{Float64}           # MWh per bus
    charge_power::Vector{Float64}       # MW per bus
    discharge_power::Vector{Float64}    # MW per bus
    charge_efficiency::Vector{Float64}  # 0-1 per bus
    discharge_efficiency::Vector{Float64}
    soc_min::Vector{Float64}            # Minimum SOC (fraction)
    soc_max::Vector{Float64}            # Maximum SOC (fraction)
    soc_initial::Vector{Float64}        # Initial SOC (fraction)
    self_discharge::Vector{Float64}     # Hourly self-discharge rate
    invest_cost_power::Vector{Float64}  # $/MW per bus
    invest_cost_capacity::Vector{Float64} # $/MWh per bus
    invest_max_power::Vector{Float64}   # Max investment MW per bus
    invest_max_capacity::Vector{Float64} # Max investment MWh per bus
    life_time::Vector{Int}
    min_duration_hours::Float64         # Minimum E/P ratio
    max_duration_hours::Float64         # Maximum E/P ratio
    maintenance_cost::Vector{Float64}
    spillage::Bool                      # Allow energy spillage
    degradation_rate::Vector{Float64}
    inertia::Vector{Float64}
    throughput_degradation_cost::Vector{Float64}  # $/MWh discharged
end
```

### NetworkConfig

```julia
struct NetworkConfig
    num_nodes::Int                              # Geographic nodes
    num_buses::Int                              # Electrical buses (>= num_nodes)
    buses::Vector{BusData}
    bus_to_node::Vector{Int}                    # Bus -> node mapping (1-indexed)
    connections::Matrix{Float64}                # Adjacency/capacity matrix
    distances::Matrix{Float64}                  # Distance matrix (km)
    base_impedance::Float64
    transmission_lines::Vector{TransmissionLineData}
    transformers::Vector{TransformerData}
    converters::Vector{ACDCConverterData}
    freq_converters::Vector{FrequencyConverterData}
end
```

### MasterProblemInput

Input structure for the capacity expansion model.

```julia
@kwdef struct MasterProblemInput
    generators::Vector{GeneratorConfig}
    batteries::Vector{BatteryConfig}
    technologies::Vector{TechnologyConfig}
    battery_technologies::Vector{BatteryTechnologyConfig}
    network::NetworkConfig
    temporal::TemporalConfig
    years::Vector{Int}
    demand::Matrix{Float64}                     # Base demand (hours x buses)
    discount_rate::Float64
    demand_growth::Float64
    max_annual_investment::Float64
    # RE targets
    target_re_penetration::Float64
    initial_re_penetration::Float64
    min_re_increment::Float64
    max_re_increment::Float64
    # Representative days
    representative_days_per_year::Int
    min_day_separation::Int
    # TSAM (Time Series Aggregation Method)
    use_tsam::Bool
    tsam_period_start_hours::Vector{Vector{Int}}
    tsam_period_weights::Vector{Vector{Float64}}
    tsam_chronological_order::Vector{Vector{Int}}
    tsam_inter_period_linking::Bool
    # Penalty coefficients
    loss_of_load_penalty::Float64
    curtailment_penalty::Float64
    max_curtailment_ratio::Float64
    slack_penalty::Float64
    # ... additional fields
end
```

### TechnologyConfig / BatteryTechnologyConfig

Investment candidate types (per-technology, not per-generator):

```julia
struct TechnologyConfig
    name::String
    type::String                        # "Renewable" or "Non-renewable"
    fuel::String
    invest_cost::Vector{Float64}        # $/MW per bus
    invest_max::Vector{Float64}         # Max cumulative MW per bus
    fixed_cost::Vector{Float64}
    fuel_cost::Vector{Float64}
    availability::Matrix{Float64}       # (hours x buses)
    life_time::Int
    # ... additional fields
end

struct BatteryTechnologyConfig
    name::String
    invest_cost_power::Vector{Float64}  # $/MW
    invest_cost_capacity::Vector{Float64} # $/MWh
    invest_max_power::Vector{Float64}
    invest_max_capacity::Vector{Float64}
    charge_efficiency::Vector{Float64}
    discharge_efficiency::Vector{Float64}
    life_time::Int
    min_duration_hours::Float64
    max_duration_hours::Float64
    # ... additional fields
end
```

### MGAResult

The result container for both `run_mga_spores` (classical MGA) and `run_spores` (SPORES sweep). A back-compat constructor defaults `objective_labels` to `["hsj_diversity", …]` when callers do not supply it, so pre-Phase-2 code paths continue to compile unchanged.

```julia
struct MGAResult
    alternatives::Vector{MasterProblemResult}
    num_alternatives::Int
    slack_fraction::Float64
    optimal_cost::Float64
    alternative_costs::Vector{Float64}
    diversity_objectives::Vector{Float64}  # one entry per non-optimal alt
    objective_labels::Vector{String}       # Phase 2: tags each non-optimal alt
                                           # with the SPORES objective that
                                           # produced it
end
```

`objective_labels[k]` matches the [`SporesObjective`](../api/config-schema.md#sporesobjective) value (`"hsj_diversity"`, `"min_total_build"`, `"max_tech_equity"`, `"max_regional_equity"`, `"evolutionary_dist"`) for the $k$-th non-optimal alternative. The cost-optimal seed is implicit at index 1 of `alternatives`.

---

## Key Functions

### Power System (Operational Dispatch)

| Function | Signature | Description |
|----------|-----------|-------------|
| `create_power_system` | `(input::PowerSystemInput) -> (Model, PowerSystemVariables)` | Build complete operational dispatch JuMP model with all variables, constraints, and objective. |
| `build_variables!` | `(model, input) -> PowerSystemVariables` | Create all decision variables: `gen_output[g,b,t]`, `bat_charge[b,n,t]`, `bat_discharge[b,n,t]`, `bat_soc[b,n,t]`, `curtailment[b,t]`, `loss_of_load[b,t]`, etc. |
| `build_objective!` | `(model, vars, input)` | Build cost minimization objective: fuel costs + fixed costs + start-up costs + penalty terms. |
| `add_generator_constraints!` | `(model, vars, input)` | Generator capacity limits, ramp rates, minimum up/down times. For units with `rated_power[b]=0` and no investment potential, constrains `gen_output[g,b,t] <= 0` to prevent free generation. |
| `add_battery_constraints!` | `(model, vars, input)` | SOC dynamics, charge/discharge limits, cyclic SOC constraint (`SOC[b,n,T] == SOC_initial`). For zero-capacity batteries, constrains all variables to zero. |
| `add_demand_constraints!` | `(model, vars, input)` | Power balance: `sum(gen) + sum(discharge) - sum(charge) + loss_of_load - curtailment == demand` per bus per timestep. For multi-bus networks, delegates to DC power flow KCL. |
| `add_reserve_constraints!` | `(model, vars, input)` | Static reserve (percentage of demand) and dynamic reserve (largest unit contingency). |
| `add_inertia_constraints!` | `(model, vars, input)` | System inertia floor from synchronous generators. |
| `add_curtailment_constraints!` | `(model, vars, input)` | Constrains total curtailment to `max_curtailment_ratio * sum(renewable_generation)`. |
| `add_renewable_constraint!` | `(model, vars, input)` | RE penetration target: `renewable_gen >= target * total_gen`. |
| `add_co2_constraint!` | `(model, vars, input)` | CO2 budget: `sum(fuel_gen * emission_factor) <= co2_budget`. |
| `add_ev_constraints!` | `(model, vars, input)` | EV charging scheduling and V2G dispatch with SOC tracking. |
| `add_sectoral_demand_constraints!` | `(model, vars, input)` | Per-sector loss-of-load with criticality-weighted penalties and demand shifting. |
| `add_n1_security_constraints!` | `(model, vars, input)` | N-1 contingency constraints for transmission and generation. |
| `extract_solution` | `(model, vars, input) -> PowerSystemResult` | Extract all variable values into a structured result. |

### Master Problem (Capacity Expansion)

| Function | Signature | Description |
|----------|-----------|-------------|
| `create_master_problem` | `(input::MasterProblemInput) -> (Model, MasterProblemVariables, targets)` | Build multi-year capacity expansion model. |
| `calculate_target_ratios` | `(initial, target, years) -> Vector{Float64}` | Compute annual RE target progression (linear interpolation). |
| `build_master_variables!` | `(model, input) -> MasterProblemVariables` | Investment variables: `gen_invest[tech,bus,year]`, `bat_invest_power[tech,bus,year]`, `bat_invest_capacity[tech,bus,year]`, `trans_invest[line,year]`. |
| `add_investment_constraints!` | `(model, vars, input)` | Cumulative investment limits per technology per bus. |
| `add_budget_constraints!` | `(model, vars, input)` | Annual budget caps. |
| `add_capacity_adequacy_constraints!` | `(model, vars, input)` | Ensures installed capacity meets peak demand plus reserve margin. |
| `add_re_target_constraints!` | `(model, vars, input, targets)` | RE penetration equality per year. |
| `add_re_increment_constraints!` | `(model, vars, input)` | Year-over-year RE penetration change limits. |
| `add_representative_days_validation!` | `(model, vars, input)` | Adds operational feasibility constraints using representative days (peak demand or TSAM clusters). Creates sub-problem variables and constraints per day per year. |
| `add_day_operational_constraints!` | `(model, day_vars, input, ...)` | Detailed operational constraints for a single representative day: generator dispatch, battery SOC dynamics (with cyclic constraint), power balance, reserves. |
| `build_master_objective!` | `(model, vars, input)` | NPV of investment costs + operational costs from representative days. |
| `solve_with_npv_iteration` | `(model, vars, input, ...) -> NPVIterationResult` | Iterative retirement: solve, compute unit NPV, force negative-NPV retirements, re-solve. |
| `extract_master_solution` | `(model, vars, input) -> MasterProblemResult` | Extract investment/retirement decisions per year per technology per bus. |
| `build_cumulative_capacity_expressions` | `(model, vars, input)` | Build JuMP expressions for cumulative installed capacity per technology per bus per year. |

### Multi-System and Stochastic Extensions

| Function | Description |
|----------|-------------|
| `create_multi_system_master_problem(input)` | Multi-system coordination with inter-system links (shared transmission, coordinated investment). |
| `add_inter_system_constraints!(model, ...)` | Inter-system transfer and coordination constraints. |
| `create_stochastic_master_problem(input)` | Two-stage stochastic programming with scenario trees. First stage: investment decisions. Second stage: operational dispatch under uncertainty. |
| `apply_scenario_multipliers(input, scenario)` | Apply demand/cost/availability multipliers for a stochastic scenario. |

### Transmission DC

| Function | Description |
|----------|-------------|
| `TransmissionDC(network)` | Construct DC power flow model from network topology. Builds incidence matrix, finds independent cycles, computes line reactances. |
| `build_incidence_matrix(buses, lines)` | Bus-line incidence matrix A where A[b,l] = +1/-1. |
| `find_cycles(buses, lines)` | Detect independent network cycles using graph theory. |
| `add_dc_constraints!(model, dc, vars, input)` | KCL power balance with angle-based flow and cycle constraints. Prevents self-loop transfers (`i == j && continue`). |
| `add_line_capacity_constraints!(model, ...)` | Thermal line limits (both directions). |
| `add_converter_constraints!(model, ...)` | AC/DC and frequency converter power transfer with directional efficiencies. |

### MGA (classical HSJ loop)

| Function | Description |
|----------|-------------|
| `run_mga_spores(input; num_alternatives, slack_fraction, …)` | Run the classical MGA Hop-Skip-Jump algorithm. Returns an `MGAResult` whose `objective_labels` are all `"hsj_diversity"`. |
| `compute_frequency_scores(alternatives, input; investment_threshold)` | Compute SPORES frequency scores: `score[var] = 1 - 2 * frequency[var]`. Variables appearing in many alternatives get negative scores. |
| `set_spores_objective!(model, vars, input, frequency_scores)` | Replace the objective with `max sum(score[var] * var / var_max)` to maximize diversity. |

### SPORES (Phase 2)

| Function | Description |
|----------|-------------|
| `run_spores(input; objectives::Vector{Symbol}, slack_fraction, …)` | Run the SPORES sweep — one alternative per entry in `objectives`. Returns an `MGAResult` whose `objective_labels[k]` matches `String(objectives[k])`. |
| `apply_spores_objective!(model, vars, input, objective::Symbol; frequency_scores, reference_solution)` | Dispatcher. Routes a SPORES objective `Symbol` to the matching `set_*_objective!`. Cleans up aux variables from the previous objective via `_clear_spores_aux!` before installing the new one. |
| `set_min_build_objective!(model, vars, input)` | $\min \sum I^{tech} + \sum I^{bat,P} + \sum I^{tr}$ — see [SPORES-1](../formulation/capacity-expansion.md#1510-minimum-total-build-objective). |
| `set_tech_equity_objective!(model, vars, input)` | $\min M$ s.t. $\sum_{n,y} I^{tech}_{y,t,n}/\bar{I}_{t,n} \leq M$ $\forall t$ — see [SPORES-2](../formulation/capacity-expansion.md#1511-technology-equity-objective). Adds one auxiliary variable $M$ and $|\mathcal T|$ constraints. |
| `set_regional_equity_objective!(model, vars, input)` | $\min M$ s.t. $\sum_{t,y} I^{tech}_{y,t,n}/\bar{I}_{t,n} + \sum_{b,y} I^{bat,P}_{y,b,n}/\bar{I}^P_{b,n} \leq M$ $\forall n$ — see [SPORES-3](../formulation/capacity-expansion.md#1512-regional-equity-objective). The spatially-explicit objective at the heart of the SPORES name. |
| `set_evolutionary_distance_objective!(model, vars, input, reference_solution)` | $\max \sum \|I - I^{ref}\|/\bar{I}$ via L1 linearisation with positive/negative deviation aux variables — see [SPORES-4](../formulation/capacity-expansion.md#1513-evolutionary-distance-objective). Typically called with the cost-optimal solution as the reference. |

Auxiliary variables and constraints installed by any of the above are tracked in `model[:_spores_objective_aux]` and deleted by `_clear_spores_aux!(model)` at the start of the next `apply_spores_objective!` call, so a sweep loop can reuse one JuMP model across all objectives without unbounded growth.

### Utility Functions

| Function | Description |
|----------|-------------|
| `create_optimizer(; solver_name, threads, time_limit, gap, verbose)` | Configure a JuMP optimizer with solver-specific parameter mapping. Supports HiGHS, Gurobi, CPLEX, SCIP, Xpress, CBC, GLPK. |
| `diagnose_infeasibility(model)` | Identify binding and conflicting constraints in an infeasible model. Uses `compute_conflict!()` if supported by the solver. |
| `export_solution_to_dict(model, vars, input)` | Convert the full solution to a Julia Dict for Python consumption. |
| `log_solution_summary(result, input)` | Print a formatted summary of the solution (generation mix, costs, RE penetration, investment decisions). |

---

## Constraint Details

### Battery Cyclic SOC

Ensures battery SOC returns to initial level at the end of each representative day:

```julia
@constraint(model, day_vars.bat_soc[b, n, hours] == initial_soc)
```

Prevents batteries from acting as infinite generators by accumulating energy across periods.

### Curtailment Limit

Constrains curtailment to a fraction of total renewable generation:

```julia
if input.max_curtailment_ratio < 1.0
    total_re_gen = sum(gen_output[g, n, t] for g in renewable_gens, n, t)
    total_curt = sum(curtailment[n, t] for n, t)
    @constraint(model, total_curt <= input.max_curtailment_ratio * total_re_gen)
end
```

### Age-Based Retirement

Units are retired based on age exceeding lifetime:

| Unit Type | Age Formula | Active Condition |
|-----------|-------------|-----------------|
| Existing | `age = initial_age + (year_idx - 1)` | `age < lifetime` |
| Investment | `age = year_idx - investment_year` | `age < lifetime` |

Pure LP formulation (no binary life extension variables).

### Free Generation Prevention

When a generator has `rated_power[bus] = 0` and no investment potential, its output is explicitly constrained to zero:

```julia
if rated <= 0 && !(is_dev && gen.invest_max[n] > 0)
    for t in 1:hours
        @constraint(model, vars.gen_output[g, n, t] <= 0)
    end
    continue
end
```

Without this constraint, unconstrained variables can take any non-negative value at zero cost, creating "free energy" in the power balance.

### PWL Transmission Losses

Piecewise linear approximation of quadratic transmission losses:

```julia
# P_loss(f) = g_l * f^2  approximated by N linear segments
# Marginal slope for segment k: m_k = g_l * (2k-1) * delta_f
# where delta_f = f_max / N and g_l = R / (R^2 + X^2)
```

Convexity of the quadratic loss function ensures the PWL approximation is exact at segment boundaries without requiring binary variables.
