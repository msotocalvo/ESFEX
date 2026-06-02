# Julia Development

## Module Structure

The Julia code lives in `src/esfex/julia/`:

```
src/esfex/julia/
├── Project.toml                    # Dependencies
├── Manifest.toml                   # Lockfile
└── src/
    ├── ESFEX.jl                   # Module entry point
    ├── types.jl                    # Type definitions (~700 lines)
    ├── power_system.jl             # Operational dispatch (~2500 lines)
    ├── master_problem.jl           # Capacity expansion (~3600 lines)
    ├── transmission_dc.jl          # DC power flow (~760 lines)
    ├── primary_energy.jl           # Fuel supply chain (~1580 lines)
    └── electrolyzer.jl             # Electrolyzers (~290 lines)
```

### Module Entry Point

```julia
# ESFEX.jl
module ESFEX
    using JuMP
    using HiGHS
    using LinearAlgebra

    include("types.jl")
    include("transmission_dc.jl")
    include("transmission_ac.jl")
    include("power_system.jl")
    include("primary_energy.jl")
    include("master_problem.jl")
    include("electrolyzer.jl")

    # Exported functions
    export create_power_system, create_master_problem
    export create_optimizer, diagnose_infeasibility
    export extract_solution, export_solution_to_dict
    # ... all public API
end
```


---


## Type System

Input data flows through strongly-typed structs in `types.jl`:

### Core Types

```julia
struct GeneratorConfig
    name::String
    type::String                    # "Renewable" or "Non-renewable"
    fuel::String
    rated_power::Vector{Float64}    # Per node
    min_power::Vector{Float64}
    efficiency_rated::Vector{Float64}
    efficiency_min::Vector{Float64}
    ramp_up::Vector{Float64}
    ramp_down::Vector{Float64}
    min_up_time::Vector{Int}
    min_down_time::Vector{Int}
    fuel_cost::Vector{Float64}
    fixed_cost::Vector{Float64}
    maintenance_cost::Vector{Float64}
    start_up_cost::Vector{Float64}
    invest_cost::Vector{Float64}
    invest_max::Vector{Float64}
    availability::Matrix{Float64}   # (hours x nodes)
    life_time::Vector{Int}
    initial_age::Vector{Int}
    degradation_rate::Vector{Float64}
    inertia::Vector{Float64}
end
```

### Convention: Per-Node Arrays

Most generator and battery parameters are `Vector{Float64}` of length `num_nodes`, allowing different technical parameters per geographic location.

### Type System Conventions

Conventions for new types in `types.jl`:

**Immutable by default.** Use `struct` (not `mutable struct`) for input data. Immutability prevents accidental modification and enables compiler optimizations.

```julia
# Correct: immutable input data
struct MyConfig
    limit::Float64
    enabled::Bool
end

# Only use mutable when truly needed (e.g., accumulating results)
mutable struct MyResult
    total_cost::Float64
    iterations::Int
end
```

**Parametric types for flexibility.** When a field could be `Int` or `Float64` depending on the caller:

```julia
struct Threshold{T<:Real}
    value::T
    penalty::Float64
end
```

**Composite input structs.** Group related parameters into nested structs. The top-level input struct (`PowerSystemInput`, `MasterProblemInput`) aggregates sub-structs:

```julia
struct CO2BudgetConfig
    enabled::Bool
    annual_budget::Float64     # tonnes/year
    penalty::Float64           # $/tonne violation
end

struct ReserveConfig
    enabled::Bool
    spinning_fraction::Float64
    penalty::Float64
end

# Top-level input includes both
struct PowerSystemInput
    generators::Vector{GeneratorConfig}
    batteries::Vector{BatteryConfig}
    co2_budget::CO2BudgetConfig
    reserves::ReserveConfig
    # ...
end
```

**Naming conventions for types:**

| Kind | Convention | Example |
|------|-----------|---------|
| Input config | `XxxConfig` | `GeneratorConfig`, `CO2BudgetConfig` |
| Model variables | `XxxVariables` | `PowerSystemVariables` |
| Solution output | `XxxResult` | `PowerSystemResult`, `MGAResult` |
| Technology specs | `XxxTechnologyConfig` | `TechnologyConfig`, `BatteryTechnologyConfig` |

**Default constructors.** Use keyword constructors for types with many fields:

```julia
Base.@kwdef struct SolverConfig
    threads::Int = 4
    time_limit::Float64 = 300.0
    gap::Float64 = 0.01
    verbose::Bool = false
end

# Usage: SolverConfig(threads=8, verbose=true)
```

### Adding a New Type

1. Define the struct in `types.jl`
2. Add it to the relevant input struct (`PowerSystemInput` or `MasterProblemInput`)
3. Update the Python adapter in `adapters.py` to construct the Julia type
4. Add constraints in the relevant `.jl` file


---


## Constraint Development

### Pattern: Adding a New Constraint

```julia
function add_my_constraints!(model::Model, vars::Variables, input::PowerSystemInput)
    hours = input.temporal.hours
    num_nodes = input.network.num_nodes

    for t in 1:hours
        for n in 1:num_nodes
            @constraint(model,
                vars.my_variable[n, t] <= input.my_parameter[n],
                base_name = "my_constraint_n$(n)_t$(t)"
            )
        end
    end
end
```

### Naming Conventions for Constraints

Use descriptive base names that include indices:

```julia
# Good
@constraint(model, ..., base_name = "gen_capacity_g$(g)_n$(n)_t$(t)")
@constraint(model, ..., base_name = "bat_soc_dynamics_b$(b)_n$(n)_t$(t)")

# Avoid
@constraint(model, ..., base_name = "c1")
```

### Handling Inactive Units

For generators or batteries with zero capacity at a node and no investment potential, explicitly bound variables to zero:

```julia
rated = gen.rated_power[n]
is_dev = input.mode == "development"
invest_max = gen.invest_max[n]

if rated <= 0 && !(is_dev && invest_max > 0)
    # CRITICAL: Bound variables to zero, don't just skip
    for t in 1:hours
        @constraint(model, vars.gen_output[g, n, t] <= 0)
    end
    continue
end
```

This prevents the "free generation" bug where unconstrained variables take arbitrary values.

### Conditional Constraints

```julia
# Only in unit commitment mode
if input.mode == "unit_commitment"
    # Binary commitment variables and min up/down time
end

# Only if CO2 budget is enabled
if input.co2_budget.enabled
    @constraint(model, sum(emissions) <= input.co2_budget.annual_budget + violation)
end

# Only if curtailment limit is active
if input.max_curtailment_ratio < 1.0
    @constraint(model, sum(curtailment) <= ratio * sum(renewable_gen))
end
```

### Constraint Implementation Patterns

#### Cyclic Constraints

For storage elements (batteries, EV fleets), constrain the final time step SOC to equal the initial state:

```julia
function add_cyclic_soc_constraint!(model, vars, input)
    hours = input.temporal.hours
    for b in 1:length(input.batteries)
        for n in 1:input.network.num_nodes
            initial_soc = input.batteries[b].initial_soc[n]
            @constraint(model,
                vars.bat_soc[b, n, hours] == initial_soc,
                base_name = "bat_soc_cyclic_b$(b)_n$(n)"
            )
        end
    end
end
```

Without this constraint, batteries discharge fully each window without replenishing.

#### Linking Constraints Across Time Steps

Ramp rate constraints link consecutive time steps; handle the first time step separately:

```julia
function add_ramp_constraints!(model, vars, gen, g, n, hours)
    ramp_up = gen.ramp_up[n]
    ramp_down = gen.ramp_down[n]

    for t in 2:hours
        @constraint(model,
            vars.gen_output[g, n, t] - vars.gen_output[g, n, t-1] <= ramp_up,
            base_name = "ramp_up_g$(g)_n$(n)_t$(t)"
        )
        @constraint(model,
            vars.gen_output[g, n, t-1] - vars.gen_output[g, n, t] <= ramp_down,
            base_name = "ramp_down_g$(g)_n$(n)_t$(t)"
        )
    end
end
```

#### Big-M Constraints

Use Big-M formulation for conditionally active constraints (e.g., minimum power output only when a generator is on):

```julia
# gen_status is binary (1 = on, 0 = off)
rated = gen.rated_power[n]
min_pwr = gen.min_power[n]

for t in 1:hours
    # Output <= rated * status  (off => output = 0)
    @constraint(model,
        vars.gen_output[g, n, t] <= rated * vars.gen_status[g, n, t],
        base_name = "gen_max_g$(g)_n$(n)_t$(t)"
    )

    # Output >= min_power * status  (on => output >= min_power)
    @constraint(model,
        vars.gen_output[g, n, t] >= min_pwr * vars.gen_status[g, n, t],
        base_name = "gen_min_g$(g)_n$(n)_t$(t)"
    )
end
```

#### Network Constraints (Self-Loop Prevention)

Always skip self-loops when creating transfer variables to prevent free energy injection:

```julia
for i in 1:num_nodes
    for j in 1:num_nodes
        i == j && continue  # CRITICAL: no self-loops
        for t in 1:hours
            @variable(model, trans[i, j, t] >= 0)
        end
    end
end
```

A self-loop `trans[i, i, t]` appears as incoming power to node `i` without any corresponding outflow, effectively creating energy from nothing.

#### Age-Based Retirement

Pure LP retirement (no binary variables) using age thresholds:

```julia
function is_unit_active(unit, year_idx)
    # Existing units: age advances each year
    age = unit.initial_age + (year_idx - 1)
    return age < unit.life_time
end

function is_investment_active(invest_year, current_year_idx, lifetime)
    # Investments: age counted from investment year
    age = current_year_idx - invest_year
    return age >= 0 && age < lifetime
end
```


---


## Variable Conventions

### Standard Variable Dimensions

| Variable | Indices | Shape |
|----------|---------|-------|
| `gen_output[g, n, t]` | generator, node, time | (G, N, T) |
| `bat_charge[b, n, t]` | battery, node, time | (B, N, T) |
| `bat_soc[b, n, t]` | battery, node, time | (B, N, T) |
| `power_flow[i, j, t]` | from_node, to_node, time | (N, N, T) |
| `loss_load[n, t]` | node, time | (N, T) |
| `curtailment[g, n, t]` | generator, node, time | (G, N, T) |

### Variable Naming

```julia
# Variables
gen_output      # Generator electrical output (MW)
gen_status      # Generator on/off status (binary in UC, continuous in ED)
gen_startup     # Generator startup indicator
gen_shutdown    # Generator shutdown indicator
bat_charge      # Battery charging power (MW)
bat_discharge   # Battery discharging power (MW)
bat_soc         # Battery state of charge (MWh)
bat_spillage    # Battery spillage (MW)
loss_load       # Unserved energy (MW)
curtailment     # Renewable curtailment (MW)
ev_charge       # EV fleet charging (MW)
ev_v2g          # EV vehicle-to-grid (MW)
ev_soc          # EV fleet SOC (MWh)
voltage_angle   # Bus voltage angle (radians)
```


---


## Objective Function

Minimizes total operational cost:

```julia
function build_objective!(model, vars, input)
    # Components:
    # 1. Generator fuel costs
    # 2. Generator maintenance costs
    # 3. Generator start-up costs (UC mode)
    # 4. Battery maintenance costs
    # 5. Load shedding penalty (loss_of_load x penalty)
    # 6. Reserve deficit penalties
    # 7. Curtailment penalty
    # 8. CO2 emission costs
    # 9. Inertia deficit penalty
    # 10. EV demand loss penalty
    # 11. RE penetration shortfall penalty
    # 12. Electrolyzer operating costs
    # 13. Primary energy costs

    @objective(model, Min, sum_of_all_cost_terms)
end
```


---


## Solution Extraction

```julia
function extract_solution(model, vars, input)
    result = PowerSystemResult()

    # Extract variable values
    result.generation = value.(vars.gen_output)
    result.battery_charge = value.(vars.bat_charge)
    result.objective_value = objective_value(model)

    # Extract dual variables (electricity prices)
    if has_duals(model)
        result.nodal_prices = dual.(model[:power_balance])
    end

    return result
end
```


---


## Performance Tips

### Memory Allocation

- Pre-allocate arrays instead of growing them
- Use views (`@view`) for array slices passed to functions
- Avoid creating temporary arrays in hot loops

```julia
# Good: Pre-allocated
output = zeros(num_generators, num_nodes, hours)
for g in 1:num_generators
    output[g, :, :] .= value.(vars.gen_output[g, :, :])
end

# Avoid: Temporary allocations
for g in 1:num_generators
    output = vcat(output, value.(vars.gen_output[g, :, :]))  # Allocates new array
end
```

### Constraint Generation

- Use `@constraints` macro for batch constraint creation when possible
- Avoid string interpolation in tight loops (use `base_name` sparingly for very large models)
- For models with thousands of constraints, consider dropping `base_name` entirely and relying on constraint indexing

```julia
# Fast: No string allocation per constraint
for t in 1:hours, n in 1:num_nodes
    @constraint(model, vars.gen_output[g, n, t] <= rated)
end

# Slower but debuggable: Named constraints
for t in 1:hours, n in 1:num_nodes
    @constraint(model, vars.gen_output[g, n, t] <= rated,
                base_name = "gen_cap_g$(g)_n$(n)_t$(t)")
end
```

Named constraints aid debugging; remove them for production runs with large models.

### Solver Configuration

```julia
function create_optimizer(; threads=4, time_limit=300.0, gap=0.01, verbose=false)
    optimizer = optimizer_with_attributes(
        HiGHS.Optimizer,
        "threads" => threads,
        "time_limit" => time_limit,
        "mip_rel_gap" => gap,
        "output_flag" => verbose,
        "presolve" => "on",
        "parallel" => "on",
    )
    return optimizer
end
```


---


## Debugging

### Infeasibility Diagnosis

```julia
# After solve, if infeasible:
if termination_status(model) == MOI.INFEASIBLE
    diagnose_infeasibility(model)
    # Prints conflicting constraints
end
```

`diagnose_infeasibility` computes an Irreducible Infeasible Subsystem (IIS) when the solver supports it, listing the minimal set of conflicting constraints.

### Model Statistics

```julia
println("Variables: ", num_variables(model))
println("Constraints: ", num_constraints(model))
println("Objective sense: ", objective_sense(model))
```

### Exporting the Model

```julia
# Write to MPS format for external debugging
write_to_file(model, "debug_model.mps")

# Write to LP format (human-readable)
write_to_file(model, "debug_model.lp")
```

LP format is human-readable for small models. For larger models (10,000+ constraints), use MPS format and inspect with a solver CLI tool.

### Inspecting Variable Values

```julia
# Single variable
println("gen_output[1,1,1] = ", value(vars.gen_output[1, 1, 1]))

# Full slice for one generator at one node
for t in 1:hours
    println("t=$t: gen=$(value(vars.gen_output[1, 1, t]))")
end

# Check dual values for binding constraints
if has_duals(model)
    for n in 1:num_nodes
        for t in 1:hours
            price = dual(model[:power_balance][n, t])
            if abs(price) > 1e-6
                println("Node $n, t=$t: price = $price")
            end
        end
    end
end
```

### Common Debugging Scenarios

**Suspiciously low objective value.** Check for unconstrained variables created but never bounded:

```julia
# After solve
for g in 1:num_generators, n in 1:num_nodes, t in 1:hours
    val = value(vars.gen_output[g, n, t])
    if val > 0 && gen.rated_power[n] <= 0
        @warn "Generator $g producing $(val) MW at node $n with zero rated power"
    end
end
```

**All duals are zero.** Indicates free energy entering the system. Check for self-loop transfer variables or unconstrained generators (see Bug #12 and #13 in the project history).

**Solver stalls.** Check penalty coefficients. A penalty of 600,000,000 (instead of 600) causes the solver to spend excessive time exploring near-penalty solutions.


---


## Testing Julia Code

### Running Tests

```bash
cd src/esfex/julia
julia --project=. -e 'using Pkg; Pkg.test()'
```

### Writing Tests

```julia
# test/test_power_system.jl
@testset "Generator Constraints" begin
    input = create_test_input(1, 1, 24)  # 1 gen, 1 node, 24 hours
    model = create_power_system(input)
    optimize!(model)

    @test termination_status(model) == MOI.OPTIMAL
    @test objective_value(model) >= 0

    gen_output = value.(model[:gen_output])
    @test all(gen_output .>= 0)
    @test all(gen_output .<= input.generators[1].rated_power[1])
end
```

### Test Helpers

```julia
function create_test_input(num_gen, num_nodes, hours; kwargs...)
    # Create minimal valid PowerSystemInput for testing
    generators = [create_test_generator(num_nodes; kwargs...) for _ in 1:num_gen]
    # ... construct other required fields
    return PowerSystemInput(generators=generators, ...)
end
```

### Testing Constraints in Isolation

Build a minimal model to verify the feasible region of a single constraint:

```julia
@testset "Cyclic SOC Constraint" begin
    model = Model(HiGHS.Optimizer)
    set_silent(model)

    hours = 24
    initial_soc = 50.0
    capacity = 100.0

    @variable(model, 0 <= soc[t=1:hours] <= capacity)
    @variable(model, -20 <= charge[t=1:hours] <= 20)

    # SOC dynamics
    @constraint(model, soc[1] == initial_soc + charge[1])
    for t in 2:hours
        @constraint(model, soc[t] == soc[t-1] + charge[t])
    end

    # Cyclic constraint
    @constraint(model, soc[hours] == initial_soc)

    # Trivial objective
    @objective(model, Min, 0)
    optimize!(model)

    @test termination_status(model) == MOI.OPTIMAL
    @test isapprox(value(soc[hours]), initial_soc, atol=1e-6)

    # Verify that total charge over the cycle is zero
    total_charge = sum(value.(charge))
    @test isapprox(total_charge, 0.0, atol=1e-6)
end
```


---


## Python-Julia Bridge

### How Python Calls Julia

The adapter converts Python data structures to Julia types:

```python
# In adapters.py
class PowerSystemAdapter:
    def _create_input(self, ...):
        jl = get_esfex_module()

        # Convert Python dict to Julia GeneratorConfig
        gen_config = jl.GeneratorConfig(
            name=gen["name"],
            type=gen["type"],
            rated_power=jl.Vector[jl.Float64](gen["rated_power"]),
            # ...
        )

        input = jl.PowerSystemInput(
            generators=jl.Vector[jl.GeneratorConfig]([gen_config]),
            # ...
        )

        return jl.create_power_system(input)
```

### Bridge Type Mapping

| Python Type | Julia Type | Notes |
|------------|-----------|-------|
| `float` | `Float64` | Direct mapping |
| `int` | `Int64` | Direct mapping |
| `bool` | `Bool` | Direct mapping |
| `str` | `String` | Direct mapping |
| `list[float]` | `Vector{Float64}` | Wrap with `jl.Vector[jl.Float64](...)` |
| `list[int]` | `Vector{Int}` | Wrap with `jl.Vector[jl.Int](...)` |
| `np.ndarray` (2D) | `Matrix{Float64}` | Automatic conversion via juliacall |
| `None` | `nothing` | Maps to Julia `Nothing` |

### Adding a New Feature

Steps to add a new optimization feature:

1. **Julia side** (`types.jl`): Add new fields to input structs
2. **Julia side** (relevant `.jl`): Add constraint/variable/objective code
3. **Python side** (`adapters.py`): Add field mapping in adapter
4. **Python side** (`schema.py`): Add Pydantic field for configuration
5. **Python side** (`runner.py`): Wire the new feature into the orchestration loop
6. **Tests**: Add Julia and Python tests
7. **Docs**: Update formulation and API documentation

### End-to-End Example: Adding a Hydrogen Storage Constraint

Every file touched when adding a new constraint:

**Step 1 --- Julia type** (`types.jl`):

```julia
struct HydrogenStorageConfig
    capacity_kg::Vector{Float64}     # Per node
    charge_rate_kg::Vector{Float64}
    discharge_rate_kg::Vector{Float64}
    initial_level_kg::Vector{Float64}
    cost_per_kg::Float64
end
```

**Step 2 --- Julia constraints** (`electrolyzer.jl`):

```julia
function add_h2_storage_constraints!(model, vars, input)
    h2 = input.h2_storage
    hours = input.temporal.hours
    num_nodes = input.network.num_nodes

    for n in 1:num_nodes
        h2.capacity_kg[n] <= 0 && continue

        for t in 1:hours
            @constraint(model, vars.h2_level[n, t] <= h2.capacity_kg[n])
            @constraint(model, vars.h2_charge[n, t] <= h2.charge_rate_kg[n])
        end

        # Cyclic constraint
        @constraint(model, vars.h2_level[n, hours] == h2.initial_level_kg[n])
    end
end
```

**Step 3 --- Python adapter** (`adapters.py`):

```python
h2_config = jl.HydrogenStorageConfig(
    capacity_kg=jl.Vector[jl.Float64](sys.h2_storage.capacity_kg),
    charge_rate_kg=jl.Vector[jl.Float64](sys.h2_storage.charge_rate_kg),
    discharge_rate_kg=jl.Vector[jl.Float64](sys.h2_storage.discharge_rate_kg),
    initial_level_kg=jl.Vector[jl.Float64](sys.h2_storage.initial_level_kg),
    cost_per_kg=sys.h2_storage.cost_per_kg,
)
```

**Step 4 --- Python schema** (`schema.py`):

```python
class HydrogenStorageConfig(BaseModel):
    capacity_kg: list[float] = []
    charge_rate_kg: list[float] = []
    discharge_rate_kg: list[float] = []
    initial_level_kg: list[float] = []
    cost_per_kg: float = 5.0
```

**Step 5 --- Tests**: Write both Julia (`@testset`) and Python (`pytest`) tests validating the constraint behavior.
