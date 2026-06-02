"""
Precompile workload for the ESFEX Julia sysimage.

This script is executed by PackageCompiler during sysimage creation.
It exercises the full JuMP + HiGHS optimization pipeline with a small
representative model so that all type-specialized native code gets
baked into the sysimage.

Expected speedup: Julia startup from ~30s to <3s.
"""

using Pkg
Pkg.activate(@__DIR__)

# ── Load dependencies ──────────────────────────────────────────────
using JuMP
using JuMP: VariableRef, AffExpr
using HiGHS
using Graphs
using LinearAlgebra
using SparseArrays
using Statistics: mean
import MathOptInterface as MOI

# ── Load the ESFEX module ─────────────────────────────────────────
include(joinpath(@__DIR__, "src", "ESFEX.jl"))
using .ESFEX

# ── 1. Small LP model (matches ESFEX operational dispatch patterns) ──
println("  [precompile] Building small LP model...")
model = Model(HiGHS.Optimizer)
set_silent(model)

hours = 4
nodes = 2
ngen = 2
nbat = 1

# Multi-dimensional variables (same shapes as power_system.jl)
@variable(model, gen_output[1:ngen, 1:nodes, 1:hours] >= 0)
@variable(model, bat_charge[1:nbat, 1:nodes, 1:hours] >= 0)
@variable(model, bat_discharge[1:nbat, 1:nodes, 1:hours] >= 0)
@variable(model, bat_soc[1:nbat, 1:nodes, 1:hours] >= 0)
@variable(model, load_shed[1:nodes, 1:hours] >= 0)
@variable(model, curtailment[1:ngen, 1:nodes, 1:hours] >= 0)
@variable(model, transfer[1:nodes, 1:nodes, 1:hours])

# Demand balance constraints
demand = [100.0 120.0 110.0 130.0;
          80.0  90.0  85.0  95.0]

for n in 1:nodes, t in 1:hours
    # Balance
    @constraint(model,
        sum(gen_output[g, n, t] for g in 1:ngen) +
        bat_discharge[1, n, t] - bat_charge[1, n, t] +
        load_shed[n, t] -
        sum(curtailment[g, n, t] for g in 1:ngen) >= demand[n, t])

    # Generator limits
    for g in 1:ngen
        @constraint(model, gen_output[g, n, t] <= 200.0)
        @constraint(model, curtailment[g, n, t] <= gen_output[g, n, t])
    end

    # Battery limits
    @constraint(model, bat_charge[1, n, t] <= 50.0)
    @constraint(model, bat_discharge[1, n, t] <= 50.0)
    @constraint(model, bat_soc[1, n, t] <= 100.0)
end

# Battery SOC dynamics
for n in 1:nodes
    @constraint(model, bat_soc[1, n, 1] == 50.0 + bat_charge[1, n, 1] - bat_discharge[1, n, 1])
    for t in 2:hours
        @constraint(model, bat_soc[1, n, t] == bat_soc[1, n, t-1] +
                    bat_charge[1, n, t] - bat_discharge[1, n, t])
    end
    # Cyclic constraint
    @constraint(model, bat_soc[1, n, hours] == 50.0)
end

# Transfer constraints (simple transport model)
for i in 1:nodes, j in 1:nodes, t in 1:hours
    if i != j
        @constraint(model, transfer[i, j, t] <= 100.0)
        @constraint(model, transfer[i, j, t] >= -100.0)
        @constraint(model, transfer[i, j, t] + transfer[j, i, t] == 0)
    else
        @constraint(model, transfer[i, j, t] == 0)
    end
end

# Objective with AffExpr accumulation (matching ESFEX pattern)
cost_expr = AffExpr(0.0)
for g in 1:ngen, n in 1:nodes, t in 1:hours
    add_to_expression!(cost_expr, 10.0 + 5.0 * g, gen_output[g, n, t])
end
for n in 1:nodes, t in 1:hours
    add_to_expression!(cost_expr, 10000.0, load_shed[n, t])
    add_to_expression!(cost_expr, 0.5, bat_charge[1, n, t])
end
@objective(model, Min, cost_expr)

# Solve
println("  [precompile] Solving LP model...")
optimize!(model)

# Extract results (triggers value/dual compilation)
status = termination_status(model)
println("  [precompile] LP status: $status")
obj = objective_value(model)
println("  [precompile] LP objective: $obj")

for g in 1:ngen, n in 1:nodes, t in 1:hours
    value(gen_output[g, n, t])
end
for n in 1:nodes, t in 1:hours
    value(load_shed[n, t])
    value(bat_charge[1, n, t])
    value(bat_discharge[1, n, t])
    value(bat_soc[1, n, t])
end

# ── 2. Small MIP model (matches master problem patterns) ──────────
println("  [precompile] Building small MIP model...")
mp = Model(HiGHS.Optimizer)
set_silent(mp)

nyears = 3
ntechs = 2

@variable(mp, invest[1:ntechs, 1:nodes, 1:nyears] >= 0)
@variable(mp, retire[1:ntechs, 1:nodes, 1:nyears] >= 0)
@variable(mp, capacity[1:ntechs, 1:nodes, 1:nyears] >= 0)

# Capacity tracking
for tech in 1:ntechs, n in 1:nodes
    @constraint(mp, capacity[tech, n, 1] == 100.0 + invest[tech, n, 1] - retire[tech, n, 1])
    for y in 2:nyears
        @constraint(mp, capacity[tech, n, y] == capacity[tech, n, y-1] +
                    invest[tech, n, y] - retire[tech, n, y])
    end
end

# Investment limits
for tech in 1:ntechs, n in 1:nodes, y in 1:nyears
    @constraint(mp, invest[tech, n, y] <= 500.0)
    @constraint(mp, retire[tech, n, y] <= capacity[tech, n, y])
end

# Budget constraint
for y in 1:nyears
    @constraint(mp, sum(1000.0 * invest[tech, n, y]
                for tech in 1:ntechs, n in 1:nodes) <= 1e6)
end

# Adequacy constraint
for y in 1:nyears
    @constraint(mp, sum(capacity[tech, n, y]
                for tech in 1:ntechs, n in 1:nodes) >= 300.0)
end

mp_cost = AffExpr(0.0)
for tech in 1:ntechs, n in 1:nodes, y in 1:nyears
    add_to_expression!(mp_cost, 1000.0 * tech, invest[tech, n, y])
    add_to_expression!(mp_cost, 50.0, capacity[tech, n, y])
end
@objective(mp, Min, mp_cost)

println("  [precompile] Solving MIP model...")
optimize!(mp)
println("  [precompile] MIP status: $(termination_status(mp))")
println("  [precompile] MIP objective: $(objective_value(mp))")

for tech in 1:ntechs, n in 1:nodes, y in 1:nyears
    value(invest[tech, n, y])
    value(capacity[tech, n, y])
end

# ── 3. Graph operations (DC power flow cycle detection) ────────────
println("  [precompile] Graph operations...")
g = SimpleGraph(4)
add_edge!(g, 1, 2)
add_edge!(g, 2, 3)
add_edge!(g, 3, 4)
add_edge!(g, 4, 1)
add_edge!(g, 1, 3)
cycles = cycle_basis(g)
nv(g)
ne(g)
neighbors(g, 1)
adjacency_matrix(g)

# ── 4. Sparse + linear algebra (incidence matrices) ────────────────
println("  [precompile] Sparse algebra...")
A = sprand(20, 20, 0.2)
b = rand(20)
Matrix(A) \ b
sparse(ones(5, 5))
A' * A
norm(b)
mean(b)

# ── 5. MOI queries (constraint/variable inspection) ────────────────
println("  [precompile] MOI queries...")
num_variables(model)
num_constraints(model; count_variable_in_set_constraints=true)

println("\n  [precompile] Workload completed successfully!")
