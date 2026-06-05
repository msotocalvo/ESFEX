"""
ESFEX.jl - Power System Optimization Module

Main Julia module for the ESFEX power system optimization framework.
Provides JuMP-based optimization models for:
- PowerSystem: Operational dispatch and unit commitment
- MasterProblem: Capacity expansion planning
- PrimaryEnergy: Fuel supply chain optimization
- TransmissionDC: DC power flow using Kirchhoff formulation
"""
module ESFEX

using JuMP
using JuMP: VariableRef, AffExpr
using HiGHS
using Graphs
using LinearAlgebra
using SparseArrays
using Statistics: mean
import MathOptInterface as MOI

# Optional solver packages -- loaded on demand via `import`
# (using `import` avoids errors when packages are not installed)
const _SOLVER_MODULES = Dict{String, Any}()

# Mapping from solver name to (Julia package symbol, module symbol in Main)
const _SOLVER_PKG_MAP = Dict{String, Tuple{Symbol, String}}(
    "scip"     => (:SCIP,     "SCIP solver"),
    "xpress"   => (:Xpress,   "Xpress solver (requires license)"),
    "gurobi"   => (:Gurobi,   "Gurobi solver (requires license)"),
    "cplex"    => (:CPLEX,    "CPLEX solver (requires license)"),
    "cbc"      => (:Cbc,      "CBC solver"),
    "glpk"     => (:GLPK,     "GLPK solver"),
    "ipopt"    => (:Ipopt,    "Ipopt solver (required for ACOPF NLP)"),
    "clarabel" => (:Clarabel, "Clarabel solver (supports LP/SOCP/SDP for ACOPF)"),
    "scs"      => (:SCS,      "SCS solver (required for ACOPF SDP)"),
)

"""
    _load_solver_module(name) -> Module

Load and cache a solver module.  If the package was already imported at
top-level (e.g. from Python pre-import), use it directly — no @eval needed,
so no world-age issues on Julia 1.12+.  Falls back to @eval import when
the module has not been pre-imported.
"""
function _load_solver_module(name::String)
    if haskey(_SOLVER_MODULES, name)
        return _SOLVER_MODULES[name]
    end
    info = get(_SOLVER_PKG_MAP, name, nothing)
    info === nothing && error("Unknown solver: $name")
    pkg_sym, desc = info

    # Check if the package was pre-imported into Main (avoids world-age issues)
    if isdefined(Main, pkg_sym)
        mod = getfield(Main, pkg_sym)
        _SOLVER_MODULES[name] = mod
        return mod
    end

    # Fallback: @eval import (may trigger world-age issues from PythonCall)
    try
        @eval import $pkg_sym
        _SOLVER_MODULES[name] = @eval $pkg_sym
    catch e
        error("$desc requested but $(pkg_sym).jl is not installed. " *
              "Install with: ] add $pkg_sym")
    end
    return _SOLVER_MODULES[name]
end

function _get_solver_optimizer(name::String)
    name = lowercase(name)
    if name == "highs"
        return HiGHS.Optimizer
    elseif name in ("scip", "xpress", "gurobi", "cplex", "cbc", "glpk",
                     "ipopt", "scs")
        mod = _load_solver_module(name)
        return mod.Optimizer
    elseif name == "clarabel"
        mod = _load_solver_module(name)
        return mod.Optimizer{Float64}
    else
        error("Unknown solver: $name. Supported: highs, scip, xpress, gurobi, cplex, cbc, glpk, ipopt, clarabel, scs")
    end
end

# Include type definitions first (depends on JuMP and MOI imports above)
include("types.jl")

# Include model components
include("transmission_dc.jl")
include("transmission_ac.jl")
include("transmission_acopf.jl")
include("power_system.jl")
include("primary_energy.jl")
include("master_problem.jl")
include("benders.jl")
include("mga.jl")
include("electrolyzer.jl")
include("dcopf_benchmark.jl")
include("acopf_benchmark.jl")

# =============================================================================
# Exports
# =============================================================================

# Configuration types
export NetworkConfig, GeneratorConfig, BatteryConfig, BusData
export TechnologyConfig, BatteryTechnologyConfig
export CostSegment
export TransmissionLineData, TransformerData
export ACDCConverterData, FrequencyConverterData
export TemporalConfig, PenaltyConfig, TargetConfig, SolverSettings

# Main types
export PowerSystemInput, PowerSystemVariables, PowerSystemResult

# TransmissionDC functions
export TransmissionDC, build_incidence_matrix
export add_dc_constraints!, add_line_capacity_constraints!
export add_converter_constraints!, add_converter_objective_terms

# TransmissionAC functions (Newton-Raphson verification)
export ACPowerFlowConfig, ACPowerFlowResult
export build_ybus, classify_buses, solve_ac_power_flow
export calculate_line_flows, run_ac_power_flow
export GuiACPowerFlowInput, solve_gui_ac_power_flow

# ACOPF formulations (SOC, QC, SDP, Polar NLP, Rectangular NLP)
export ACOPFFormulation, SOCFormulation, QCFormulation, SDPFormulation
export PolarNLPFormulation, RectNLPFormulation
export ACOPFNetwork, ACOPFBranch, ACOPFVariables
export parse_acopf_formulation, setup_acopf!
export build_acopf_variables!, add_acopf_voltage_constraints!
export add_acopf_power_balance!, add_acopf_line_limits!
export extract_acopf_voltages, extract_acopf_reactive_gen

# PowerSystem functions
export create_power_system, build_variables!, build_objective!
export add_demand_constraints!, add_generator_constraints!
export add_battery_constraints!, add_reserve_constraints!
export add_inertia_constraints!
export add_curtailment_constraints!, add_renewable_constraint!, add_co2_constraint!
export extract_solution
export recover_uc_duals!
export recover_uc_duals_via_copy

# EV types
export EVConfig, EVVariables, EVResult

# Electrolyzer types and functions
export ElectrolyzerConfig, ElectrolyzerVariables, ElectrolyzerResult
export build_electrolyzer_variables!, add_electrolyzer_constraints!
export get_electrolyzer_objective_terms, extract_electrolyzer_solution

# Primary Energy types and functions
export FuelConfig, FuelInfrastructureConfig, NonElectricDemandConfig
export FuelRouteParams, TransportRoute
export PrimaryEnergyInput, PrimaryEnergyVariables, PrimaryEnergyResult
export create_primary_energy_model, create_temporal_mapping
export build_primary_energy_variables!, add_primary_energy_constraints!
export get_primary_energy_objective_terms, extract_primary_energy_solution
export couple_primary_energy_to_power_system!

# Master Problem types and functions
export SystemNodeRange
export MasterProblemInput, MasterProblemVariables, MasterProblemResult
export create_master_problem, build_master_variables!
export add_investment_constraints!, add_budget_constraints!
export add_retirement_cascade_constraints!, add_re_target_constraints!
export add_re_increment_constraints!
export add_capacity_adequacy_constraints!, add_transmission_symmetry_constraints!
export build_master_objective!, extract_master_solution

# Benders decomposition (optional master-problem solver)
export BendersResult, run_benders_decomposition
export extract_master_solution_from_benders
export calculate_target_ratios, select_representative_days
export build_cumulative_capacity_expressions

# Representative Days Validation (CRITICAL for correct RE investment)
export create_day_ps_vars!, add_day_operational_constraints!
export calculate_day_operational_cost, add_representative_days_validation!

# Multi-System Master Problem types and functions
export InterSystemLink, SystemConfig, MultiSystemMasterInput
export ExtendedMasterVariables, ExtendedMasterResult
export create_multi_system_master_problem
export add_inter_system_constraints!, build_multi_system_objective!

# Stochastic Programming types and functions
export ScenarioMultipliers, Scenario, StochasticMasterInput
export create_stochastic_master_problem
export apply_scenario_multipliers, build_stochastic_objective!

# Primary Energy Investment in Master Problem
export PrimaryEnergyInvestmentConfig
export add_primary_energy_investment_variables!
export add_primary_energy_investment_costs!

# NPV Iteration and Retirement
export UnitNPV, NPVIterationResult
export calculate_unit_npv, get_units_with_negative_npv
export force_unit_retirements!, solve_with_npv_iteration

# MGA / SPORES
export MGAResult
export run_mga_spores, compute_frequency_scores, set_spores_objective!
# SPORES roadmap (Phase 2): distinct objective functions and dispatcher
export run_spores, apply_spores_objective!
export set_min_build_objective!, set_tech_equity_objective!
export set_regional_equity_objective!, set_evolutionary_distance_objective!

export build_investment_cost_expression

# Diagnostics and Export
export diagnose_infeasibility, log_solution_summary
export export_solution_to_dict

# Utility functions
export create_optimizer

# =============================================================================
# Utility Functions
# =============================================================================

"""
    create_optimizer(; solver_name="highs", threads=4, time_limit=3600, gap=0.01, verbose=false)

Create a configured optimizer with specified parameters.

# Arguments
- `solver_name::String="highs"`: Solver name (highs, scip, xpress, gurobi, cplex, cbc, glpk)
- `threads::Int=4`: Number of solver threads
- `time_limit::Float64=3600.0`: Maximum solve time in seconds
- `gap::Float64=0.01`: MIP optimality gap tolerance
- `verbose::Bool=false`: Enable solver output

# Returns
- Configured optimizer for use with JuMP Model
"""
function create_optimizer(; solver_name::String="highs", threads::Int=4,
                          time_limit::Float64=3600.0,
                          gap::Float64=0.01, verbose::Bool=false,
                          solver_options::Dict{String, Any}=Dict{String, Any}())
    name = lowercase(solver_name)
    OptimizerType = _get_solver_optimizer(name)

    if name == "highs"
        # Base attributes
        attrs = Pair{String,Any}[
            "threads" => threads,
            "time_limit" => time_limit,
            "mip_rel_gap" => gap,
            "output_flag" => verbose,
        ]
        # Apply user-provided solver_options (override defaults)
        for (k, v) in solver_options
            push!(attrs, string(k) => v)
        end
        # LP algorithm: when the user does not pin "solver", let HiGHS choose
        # automatically (its default). The previous forced "solver"=>"simplex"
        # default was a stale workaround for an ill-conditioned DCOPF
        # formulation that has since been corrected — it also pinned HiGHS to
        # its single-threaded simplex regardless of the configured thread count.
        # Crossover off only when the user EXPLICITLY pins simplex: crossover
        # on an already-simplex-solved basis triggers spurious "Dual simplex
        # ratio test" failures.
        if get(solver_options, "solver", "") == "simplex"
            push!(attrs, "run_crossover" => "off")
        end
        return optimizer_with_attributes(OptimizerType, attrs...)
    elseif name == "scip"
        attrs = Pair{String,Any}[
            "limits/time" => time_limit,
            "limits/gap" => gap,
            "display/verblevel" => (verbose ? 4 : 0),
            # Thread count for SCIP's concurrent solver. Note: SCIP's default
            # LP relaxation solver (SoPlex) is single-threaded, so this only
            # takes effect when SCIP runs in concurrent mode.
            "parallel/maxnthreads" => threads,
        ]
        for (k, v) in solver_options
            push!(attrs, string(k) => v)
        end
        return optimizer_with_attributes(OptimizerType, attrs...)
    elseif name == "xpress"
        attrs = Pair{String,Any}[
            "THREADS" => threads,
            "MAXTIME" => round(Int, time_limit),
            "MIPRELSTOP" => gap,
            "OUTPUTLOG" => (verbose ? 1 : 0),
        ]
        for (k, v) in solver_options
            push!(attrs, string(k) => v)
        end
        return optimizer_with_attributes(OptimizerType, attrs...)
    elseif name == "gurobi"
        attrs = Pair{String,Any}[
            "Threads" => threads,
            "TimeLimit" => time_limit,
            "MIPGap" => gap,
            "OutputFlag" => (verbose ? 1 : 0),
        ]
        for (k, v) in solver_options
            push!(attrs, string(k) => v)
        end
        return optimizer_with_attributes(OptimizerType, attrs...)
    elseif name == "cplex"
        attrs = Pair{String,Any}[
            "CPXPARAM_Threads" => threads,
            "CPXPARAM_TimeLimit" => time_limit,
            "CPXPARAM_MIP_Tolerances_MIPGap" => gap,
        ]
        for (k, v) in solver_options
            push!(attrs, string(k) => v)
        end
        return optimizer_with_attributes(OptimizerType, attrs...)
    elseif name == "cbc"
        attrs = Pair{String,Any}[
            "threads" => threads,
            "seconds" => time_limit,
            "ratioGap" => gap,
        ]
        if !verbose
            push!(attrs, "logLevel" => 0)
        end
        for (k, v) in solver_options
            push!(attrs, string(k) => v)
        end
        return optimizer_with_attributes(OptimizerType, attrs...)
    elseif name == "glpk"
        attrs = Pair{String,Any}[
            "tm_lim" => round(Int, time_limit * 1000),  # GLPK uses milliseconds
            "msg_lev" => (verbose ? 3 : 0),  # GLP_MSG_OFF=0, GLP_MSG_ALL=3
        ]
        for (k, v) in solver_options
            push!(attrs, string(k) => v)
        end
        return optimizer_with_attributes(OptimizerType, attrs...)
    elseif name == "ipopt"
        attrs = Pair{String,Any}[
            "max_cpu_time" => time_limit,
            "tol" => gap,
            "print_level" => (verbose ? 5 : 0),
        ]
        for (k, v) in solver_options
            push!(attrs, string(k) => v)
        end
        return optimizer_with_attributes(OptimizerType, attrs...)
    elseif name == "clarabel"
        attrs = Pair{String,Any}[
            "time_limit" => time_limit,
            "verbose" => verbose,
        ]
        for (k, v) in solver_options
            push!(attrs, string(k) => v)
        end
        return optimizer_with_attributes(OptimizerType, attrs...)
    elseif name == "scs"
        attrs = Pair{String,Any}[
            "max_iters" => 100_000,
            "eps_abs" => 1e-6,
            "eps_rel" => 1e-6,
            "verbose" => (verbose ? 1 : 0),
        ]
        for (k, v) in solver_options
            push!(attrs, string(k) => v)
        end
        return optimizer_with_attributes(OptimizerType, attrs...)
    else
        error("Unsupported solver: $name")
    end
end

"""
    version()

Return the ESFEX.jl version string.
"""
function version()
    return "0.1.0"
end

export version

# DCOPF Benchmark
export solve_dcopf

# ACOPF Benchmark
export solve_acopf

end # module ESFEX
