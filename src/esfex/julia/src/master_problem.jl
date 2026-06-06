"""
master_problem.jl - Capacity Expansion Master Problem

Handles long-term investment decisions for:
- Generator capacity additions and retirements
- Battery storage investments
- Transmission expansion
- Representative day selection and operational validation

The Master Problem uses a two-stage structure:
1. First stage: Investment decisions (common across scenarios)
2. Second stage: Operational costs validated through representative days
"""

using JuMP: @variable, @constraint, @objective, @expression, Model, AffExpr
using JuMP: value, VariableRef, add_to_expression!, objective_value
using JuMP: termination_status, solve_time, optimize!
using JuMP: lower_bound, upper_bound, set_lower_bound, set_upper_bound
using JuMP: set_objective_function, set_objective_sense, MAX_SENSE, MIN_SENSE
using JuMP: objective_function

# =============================================================================
# Target RE Calculation
# =============================================================================

"""
    calculate_target_ratios(input::MasterProblemInput)

Calculate progressive RE penetration targets for each year.
Linear interpolation from initial to target penetration.
Applies min/max increment constraints as in Python legacy (lines 99-122).
"""
function calculate_target_ratios(input::MasterProblemInput)::Dict{Tuple{Int,Int}, Float64}
    num_years = length(input.years)
    targets = Dict{Tuple{Int,Int}, Float64}()
    n_sys = length(input.system_node_ranges)

    if n_sys == 0
        # Single global system — use global initial_re_penetration
        for y_idx in 1:num_years
            targets[(1, y_idx)] = _compute_year_target(
                y_idx, num_years, input.initial_re_penetration,
                input.target_re_penetration, input.min_re_increment,
                input.max_re_increment,
                y_idx > 1 ? targets[(1, y_idx - 1)] : 0.0
            )
        end
    else
        # Per-system targets using each system's initial_re
        for s_idx in 1:n_sys
            sys_initial = input.system_node_ranges[s_idx].initial_re
            for y_idx in 1:num_years
                targets[(s_idx, y_idx)] = _compute_year_target(
                    y_idx, num_years, sys_initial,
                    input.target_re_penetration, input.min_re_increment,
                    input.max_re_increment,
                    y_idx > 1 ? targets[(s_idx, y_idx - 1)] : 0.0
                )
            end
        end
    end

    return targets
end

function _compute_year_target(
    y_idx::Int, num_years::Int, initial_re::Float64,
    target_re::Float64, min_inc::Float64, max_inc::Float64,
    prev_target::Float64
)::Float64
    if y_idx == 1
        return initial_re
    elseif num_years == 1
        return target_re
    else
        progress = (y_idx - 1) / (num_years - 1)
        target = initial_re + progress * (target_re - initial_re)
        increment = target - prev_target
        if increment < min_inc
            target = prev_target + min_inc
        elseif increment > max_inc
            target = prev_target + max_inc
        end
        return target
    end
end

# =============================================================================
# Representative Day Selection
# =============================================================================

"""
    select_representative_days(demand, year_idx, num_days, min_separation, timesteps_per_day, timesteps_per_year)

Select representative days based on peak gross demand.

Returns vector of starting timestep indices for each representative day.
"""
function select_representative_days(
    demand::Matrix{Float64},
    year_idx::Int,
    num_days::Int,
    min_separation::Int,
    timesteps_per_day::Int,
    timesteps_per_year::Int,
)::Vector{Int}
    year_start = (year_idx - 1) * timesteps_per_year + 1
    year_end = min(year_idx * timesteps_per_year, size(demand, 1))

    if year_end < year_start
        return Int[]
    end

    year_demand = demand[year_start:year_end, :]
    num_timesteps = size(year_demand, 1)
    num_possible_days = num_timesteps ÷ timesteps_per_day

    if num_possible_days < 1
        return Int[]
    end

    daily_peaks = Float64[]
    for d in 1:num_possible_days
        start_t = (d - 1) * timesteps_per_day + 1
        end_t = d * timesteps_per_day
        day_signal = year_demand[start_t:end_t, :]
        push!(daily_peaks, maximum(sum(day_signal, dims=2)))
    end

    # Helper: check if day satisfies minimum separation from all selected
    function is_valid_day(day_idx::Int, selected::Set{Int}, min_sep::Int)::Bool
        for sel_d in selected
            if abs(day_idx - sel_d) < min_sep
                return false
            end
        end
        return true
    end

    # Helper: find day with highest peak in range satisfying constraints
    function find_best_in_range(start_d::Int, end_d::Int, excluded::Set{Int}, min_sep::Int)::Int
        best = -1
        best_peak = -Inf
        for d in start_d:end_d
            if d in excluded
                continue
            end
            if !is_valid_day(d, excluded, min_sep)
                continue
            end
            if daily_peaks[d] > best_peak
                best_peak = daily_peaks[d]
                best = d
            end
        end
        return best
    end

    # Start with global peak day
    selected_days = Int[]
    selected_indices = Set{Int}()

    peak_day = argmax(daily_peaks)
    push!(selected_days, year_start + (peak_day - 1) * timesteps_per_day)
    push!(selected_indices, peak_day)

    # Select remaining days using segment-based approach for temporal diversity
    # (matches Python legacy: divide year into segments, pick best per segment)
    remaining_needed = min(num_days, num_possible_days) - 1

    if remaining_needed > 0
        # Divide year into segments to ensure diversity across seasons
        num_segments = remaining_needed + 1  # +1 because peak day occupies one segment
        segment_size = num_possible_days ÷ num_segments

        for seg in 0:(num_segments - 1)
            if length(selected_days) >= min(num_days, num_possible_days)
                break
            end

            seg_start = seg * segment_size + 1
            seg_end = min((seg + 1) * segment_size, num_possible_days)

            best = find_best_in_range(seg_start, seg_end, selected_indices, min_separation)
            if best > 0
                push!(selected_days, year_start + (best - 1) * timesteps_per_day)
                push!(selected_indices, best)
            end
        end
    end

    return selected_days
end

# =============================================================================
# Investment Period Helpers
# =============================================================================

"""
    inv_period_year(y_idx, years_per_inv_period)

Return the investment period start year for a given year index.
Investment decisions are grouped into periods of `years_per_inv_period` years.
E.g., with years_per_inv_period=5: years 1-5 → period start 1, years 6-10 → 6.
"""
@inline function inv_period_year(y_idx::Int, years_per_inv_period::Int)::Int
    return ((y_idx - 1) ÷ years_per_inv_period) * years_per_inv_period + 1
end

"""
    is_inv_period_start(y_idx, years_per_inv_period)

Return true if y_idx is the first year of an investment period.
"""
@inline function is_inv_period_start(y_idx::Int, years_per_inv_period::Int)::Bool
    return (y_idx - 1) % years_per_inv_period == 0
end

# =============================================================================
# Variable Creation
# =============================================================================

"""
    build_master_variables!(model, input)

Create all Master Problem decision variables.
"""
function build_master_variables!(
    model::Model,
    input::MasterProblemInput
)::MasterProblemVariables
    num_years = length(input.years)
    n_buses = input.network.num_buses
    n_gen = length(input.generators)
    n_bat = length(input.batteries)

    # Investment period grouping from investment_resolution_hours
    years_per_inv_period = max(1, input.investment_resolution_hours ÷ 8760)
    num_inv_periods = cld(num_years, years_per_inv_period)  # ceiling division
    @info "Investment periods: $(num_inv_periods) periods of $(years_per_inv_period) year(s) each"

    # Technology investment variables (per-technology, not per-generator)
    n_tech = length(input.technologies)
    n_bat_tech = length(input.battery_technologies)
    tech_investment = Dict{Int, Dict{Int, Vector{VariableRef}}}()
    bat_tech_power_investment = Dict{Int, Dict{Int, Vector{VariableRef}}}()
    bat_tech_capacity_investment = Dict{Int, Dict{Int, Vector{VariableRef}}}()
    transfer_investment = Dict{Int, Dict{Tuple{Int,Int}, VariableRef}}()

    # Life extension variables
    gen_life_extension = Dict{Int, Dict{Int, Vector{Union{VariableRef, Nothing}}}}()
    bat_life_extension = Dict{Int, Dict{Int, Vector{Union{VariableRef, Nothing}}}}()

    # RE ratio — per system (sys_idx, y_idx) → variable
    # When system_node_ranges is empty, sys_idx=1 covers all buses
    re_penetration_ratio = Dict{Tuple{Int,Int}, VariableRef}()

    # Slack variables
    slack_re_target = Dict{Int, VariableRef}()
    slack_capacity = Dict{Tuple{Int,Int}, VariableRef}()
    slack_budget = Dict{Int, VariableRef}()

    b2n = input.network.bus_to_node  # bus index → node index mapping

    for y_idx in 1:num_years
        # Only create investment variables at investment period boundaries.
        # Non-start years share the same variables as their period's start year.
        if !is_inv_period_start(y_idx, years_per_inv_period)
            # Skip investment variable creation — cumulative sums iterate
            # only over period-start years via step `years_per_inv_period`.
        else
            # Technology investment per bus (per-technology, not per-generator)
            tech_investment[y_idx] = Dict{Int, Vector{VariableRef}}()
            for t in 1:n_tech
                tech = input.technologies[t]
                tech_investment[y_idx][t] = @variable(model,
                    [b=1:n_buses],
                    lower_bound = 0,
                    upper_bound = tech.invest_max[b],
                    base_name = "tech_inv_y$(y_idx)_t$(t)"
                )
            end

            # Battery technology power investment per bus
            bat_tech_power_investment[y_idx] = Dict{Int, Vector{VariableRef}}()
            for bt in 1:n_bat_tech
                btech = input.battery_technologies[bt]
                bat_tech_power_investment[y_idx][bt] = @variable(model,
                    [b=1:n_buses],
                    lower_bound = 0,
                    upper_bound = btech.invest_max_power[b],
                    base_name = "bat_tech_pow_inv_y$(y_idx)_bt$(bt)"
                )
            end

            # Battery technology capacity investment per bus
            bat_tech_capacity_investment[y_idx] = Dict{Int, Vector{VariableRef}}()
            for bt in 1:n_bat_tech
                btech = input.battery_technologies[bt]
                bat_tech_capacity_investment[y_idx][bt] = @variable(model,
                    [b=1:n_buses],
                    lower_bound = 0,
                    upper_bound = btech.invest_max_capacity[b],
                    base_name = "bat_tech_cap_inv_y$(y_idx)_bt$(bt)"
                )
            end

            # Transmission investment
            transfer_investment[y_idx] = Dict{Tuple{Int,Int}, VariableRef}()
            for i in 1:n_buses
                for j in 1:n_buses
                    if i != j && (input.network.connections[i, j] > 0 || input.network.transference_invest_max[i] > 0)
                        # Upper bound from transference_invest_max (use bus i as reference)
                        max_inv = input.network.transference_invest_max[i]
                        transfer_investment[y_idx][(i, j)] = @variable(model,
                            lower_bound = 0,
                            upper_bound = max_inv,
                            base_name = "trans_inv_y$(y_idx)_$(i)_$(j)"
                        )
                    end
                end
            end
        end  # is_inv_period_start

        # Life extension variables: continuous variables for units past lifetime
        # Allows optimizer to choose how much capacity to keep online (LP-friendly)
        gen_life_extension[y_idx] = Dict{Int, Vector{Union{VariableRef, Nothing}}}()
        for g in 1:n_gen
            gen = input.generators[g]
            gen_life_extension[y_idx][g] = Vector{Union{VariableRef, Nothing}}(nothing, n_buses)
            for n in 1:n_buses
                rated_power = gen.rated_power[n]
                lifetime = gen.life_time[n]
                initial_age = gen.initial_age[n]
                age_at_year = initial_age + y_idx - 1
                degradation_rate = gen.degradation_rate[n]
                if age_at_year >= lifetime && rated_power > 0
                    deg_factor = (1.0 - degradation_rate) ^ age_at_year
                    gen_life_extension[y_idx][g][n] = @variable(model,
                        lower_bound = 0,
                        upper_bound = rated_power * deg_factor,
                        base_name = "gen_life_ext_y$(y_idx)_g$(g)_n$(n)"
                    )
                end
            end
        end

        bat_life_extension[y_idx] = Dict{Int, Vector{Union{VariableRef, Nothing}}}()
        for bi in 1:n_bat
            bat = input.batteries[bi]
            bat_life_extension[y_idx][bi] = Vector{Union{VariableRef, Nothing}}(nothing, n_buses)
            for n in 1:n_buses
                base_power = bat.max_discharge_power[n]
                lifetime = bat.life_time[n]
                initial_age = bat.initial_age[n]
                age_at_year = initial_age + y_idx - 1
                if age_at_year >= lifetime && base_power > 0
                    bat_life_extension[y_idx][bi][n] = @variable(model,
                        lower_bound = 0,
                        upper_bound = base_power,
                        base_name = "bat_life_ext_y$(y_idx)_b$(bi)_n$(n)"
                    )
                end
            end
        end

        # RE penetration ratio — per system
        n_sys = length(input.system_node_ranges)
        if n_sys == 0
            n_sys = 1  # single global system
        end
        for s_idx in 1:n_sys
            sname = n_sys == 1 && isempty(input.system_node_ranges) ? "global" : input.system_node_ranges[s_idx].name
            re_penetration_ratio[(s_idx, y_idx)] = @variable(model,
                lower_bound = 0,
                upper_bound = 1,
                base_name = "re_ratio_s$(s_idx)_y$(y_idx)"
            )
        end

        # Slack variables
        slack_re_target[y_idx] = @variable(model,
            lower_bound = 0,
            base_name = "slack_re_y$(y_idx)"
        )

        slack_budget[y_idx] = @variable(model,
            lower_bound = 0,
            base_name = "slack_budget_y$(y_idx)"
        )

        # Slack capacity per NODE (not per bus) — generators on different buses
        # of the same node can serve demand on any bus via internal transmission
        for ni in 1:input.network.num_nodes
            slack_capacity[(y_idx, ni)] = @variable(model,
                lower_bound = 0,
                base_name = "slack_cap_y$(y_idx)_node$(ni)"
            )
        end
    end

    # Primary energy investment variables (M10)
    fuel_storage_investment = Dict{String, Dict{Int, Vector{VariableRef}}}()
    fuel_transport_investment = Dict{String, Dict{Int, Dict{Int, VariableRef}}}()

    if !isempty(input.pe_configs)
        for pe_config in input.pe_configs
            fuel_id = pe_config.fuel_id
            fuel_storage_investment[fuel_id] = Dict{Int, Vector{VariableRef}}()
            fuel_transport_investment[fuel_id] = Dict{Int, Dict{Int, VariableRef}}()

            for y_idx in 1:num_years
                # Storage investment per node per year
                fuel_storage_investment[fuel_id][y_idx] = @variable(model,
                    [n=1:n_buses],
                    lower_bound = 0,
                    upper_bound = pe_config.storage_invest_max[n],
                    base_name = "fuel_stor_inv_$(fuel_id)_y$(y_idx)"
                )

                # Transport investment per route per year
                fuel_transport_investment[fuel_id][y_idx] = Dict{Int, VariableRef}()
                for (r, route) in enumerate(input.transport_routes)
                    if haskey(route.fuel_params, fuel_id)
                        fuel_transport_investment[fuel_id][y_idx][r] = @variable(model,
                            lower_bound = 0,
                            upper_bound = pe_config.transport_invest_max,
                            base_name = "fuel_trans_inv_$(fuel_id)_y$(y_idx)_r$(r)"
                        )
                    end
                end
            end
        end
    end

    # Reservoir capacity investment variables
    reservoir_investment = Dict{Int, Dict{Int, Vector{VariableRef}}}()
    for y_idx in 1:num_years
        reservoir_investment[y_idx] = Dict{Int, Vector{VariableRef}}()
        for g in 1:n_gen
            gen = input.generators[g]
            if any(gen.reservoir_invest_max .> 0)
                reservoir_investment[y_idx][g] = @variable(model,
                    [b=1:n_buses],
                    lower_bound = 0,
                    upper_bound = gen.reservoir_invest_max[b],
                    base_name = "res_inv_y$(y_idx)_g$(g)"
                )
            end
        end
    end

    return MasterProblemVariables(
        tech_investment,
        bat_tech_power_investment,
        bat_tech_capacity_investment,
        transfer_investment,
        gen_life_extension,
        bat_life_extension,
        re_penetration_ratio,
        Dict{Tuple{Int,Int}, Tuple{Model, PowerSystemVariables}}(),
        Dict{Int, Vector{AffExpr}}(),
        slack_re_target,
        slack_capacity,
        slack_budget,
        fuel_storage_investment,
        fuel_transport_investment,
        Dict{Int, Dict{Tuple{Int,Int,Int}, VariableRef}}(),  # inter_period_soc (TSAM)
        Dict{Int, Dict{Tuple{Int,Int,Int}, VariableRef}}(),  # inter_period_reservoir (TSAM seasonal hydro)
        reservoir_investment,
        years_per_inv_period
    )
end

# =============================================================================
# Constraints
# =============================================================================

"""
    add_investment_constraints!(model, vars, input)

Add cumulative investment limit constraints.
"""
function add_investment_constraints!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput
)
    num_years = length(input.years)
    n_buses = input.network.num_buses
    b2n = input.network.bus_to_node
    n_gen = length(input.generators)
    n_bat = length(input.batteries)

    # Technology cumulative investment limits per bus
    n_tech = length(input.technologies)
    n_bat_tech = length(input.battery_technologies)
    ypp = vars.years_per_inv_period  # shorthand
    for t in 1:n_tech
        tech = input.technologies[t]
        for b in 1:n_buses
            max_total = tech.invest_max[b]
            if max_total > 0
                cumulative = sum(vars.tech_investment[y][t][b] for y in 1:ypp:num_years)
                @constraint(model,
                    cumulative <= max_total,
                    base_name = "cumul_tech_t$(t)_b$(b)"
                )
            end
        end
    end

    # Battery technology cumulative investment limits per bus
    for bt in 1:n_bat_tech
        btech = input.battery_technologies[bt]
        for b in 1:n_buses
            # Power limit
            max_power = btech.invest_max_power[b]
            if max_power > 0
                cumulative_power = sum(vars.bat_tech_power_investment[y][bt][b] for y in 1:ypp:num_years)
                @constraint(model,
                    cumulative_power <= max_power,
                    base_name = "cumul_bat_tech_pow_bt$(bt)_b$(b)"
                )
            end

            # Capacity limit
            max_cap = btech.invest_max_capacity[b]
            if max_cap > 0
                cumulative_cap = sum(vars.bat_tech_capacity_investment[y][bt][b] for y in 1:ypp:num_years)
                @constraint(model,
                    cumulative_cap <= max_cap,
                    base_name = "cumul_bat_tech_cap_bt$(bt)_b$(b)"
                )
            end
        end
    end

    # Battery technology duration constraints (energy-to-power ratio)
    # Only at investment period boundaries (same variables are shared within a period)
    for bt in 1:n_bat_tech
        btech = input.battery_technologies[bt]
        min_dur = btech.min_duration_hours
        max_dur = btech.max_duration_hours

        for y_idx in 1:ypp:num_years
            for b in 1:n_buses
                if min_dur > 0
                    @constraint(model,
                        vars.bat_tech_capacity_investment[y_idx][bt][b] >= min_dur * vars.bat_tech_power_investment[y_idx][bt][b],
                        base_name = "bat_tech_min_dur_bt$(bt)_b$(b)_y$(y_idx)"
                    )
                end
                if max_dur < Inf && max_dur > 0
                    @constraint(model,
                        vars.bat_tech_capacity_investment[y_idx][bt][b] <= max_dur * vars.bat_tech_power_investment[y_idx][bt][b],
                        base_name = "bat_tech_max_dur_bt$(bt)_b$(b)_y$(y_idx)"
                    )
                end
            end
        end
    end

    # Reservoir cumulative investment limits per bus
    for g in 1:n_gen
        gen = input.generators[g]
        for b in 1:n_buses
            max_res = gen.reservoir_invest_max[b]
            if max_res > 0 && all(haskey(vars.reservoir_investment[y], g) for y in 1:ypp:num_years)
                cumulative_res = sum(vars.reservoir_investment[y][g][b] for y in 1:ypp:num_years)
                @constraint(model,
                    cumulative_res <= max_res,
                    base_name = "cumul_res_g$(g)_b$(b)"
                )
            end
        end
    end

    # Per-node aggregate cumulative investment constraint
    # When multiple buses map to the same node, the total investment across
    # all buses at that node must not exceed the per-node limit.
    node_to_buses_inv = Dict{Int, Vector{Int}}()
    for b in 1:n_buses
        push!(get!(node_to_buses_inv, b2n[b], Int[]), b)
    end
    for t in 1:n_tech
        tech = input.technologies[t]
        for (ni, buses_at_node) in node_to_buses_inv
            length(buses_at_node) <= 1 && continue
            max_inv = tech.invest_max[buses_at_node[1]]
            max_inv > 0 || continue
            @constraint(model,
                sum(vars.tech_investment[y][t][b] for y in 1:ypp:num_years, b in buses_at_node) <= max_inv,
                base_name = "cumul_node_tech_t$(t)_n$(ni)")
        end
    end
    for bt in 1:n_bat_tech
        btech = input.battery_technologies[bt]
        for (ni, buses_at_node) in node_to_buses_inv
            length(buses_at_node) <= 1 && continue
            max_pow = btech.invest_max_power[buses_at_node[1]]
            max_cap = btech.invest_max_capacity[buses_at_node[1]]
            if max_pow > 0
                @constraint(model,
                    sum(vars.bat_tech_power_investment[y][bt][b] for y in 1:ypp:num_years, b in buses_at_node) <= max_pow,
                    base_name = "cumul_node_bat_tech_pow_bt$(bt)_n$(ni)")
            end
            if max_cap > 0
                @constraint(model,
                    sum(vars.bat_tech_capacity_investment[y][bt][b] for y in 1:ypp:num_years, b in buses_at_node) <= max_cap,
                    base_name = "cumul_node_bat_tech_cap_bt$(bt)_n$(ni)")
            end
        end
    end
end

"""
    add_transmission_symmetry_constraints!(model, vars, input)

Add symmetry constraints for transmission investment.
Ensures transfer_investment[i,j] == transfer_investment[j,i].
"""
function add_transmission_symmetry_constraints!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput
)
    num_years = length(input.years)
    n_buses = input.network.num_buses
    ypp = vars.years_per_inv_period

    # Only add at investment period starts (same variables within period)
    for y_idx in 1:ypp:num_years
        for i in 1:n_buses
            for j in (i+1):n_buses
                # Only add constraint if both directions exist
                if haskey(vars.transfer_investment[y_idx], (i, j)) &&
                   haskey(vars.transfer_investment[y_idx], (j, i))
                    @constraint(model,
                        vars.transfer_investment[y_idx][(i, j)] ==
                        vars.transfer_investment[y_idx][(j, i)],
                        base_name = "trans_sym_$(i)_$(j)_y$(y_idx)"
                    )
                end
            end
        end
    end
end

"""
    add_budget_constraints!(model, vars, input)

Add annual investment budget constraints.
"""
function add_budget_constraints!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput
)
    num_years = length(input.years)
    n_buses = input.network.num_buses
    b2n = input.network.bus_to_node
    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    ypp = vars.years_per_inv_period

    # Budget constraints only at investment period starts
    for y_idx in 1:ypp:num_years
        annual_cost = AffExpr(0.0)

        # Technology investment costs
        for t in 1:length(input.technologies)
            tech = input.technologies[t]
            for n in 1:n_buses
                add_to_expression!(annual_cost,
                    vars.tech_investment[y_idx][t][n],
                    tech.invest_cost[n])
            end
        end

        # Battery technology investment costs
        for bt in 1:length(input.battery_technologies)
            btech = input.battery_technologies[bt]
            for n in 1:n_buses
                # Power cost
                add_to_expression!(annual_cost,
                    vars.bat_tech_power_investment[y_idx][bt][n],
                    btech.invest_cost_power[n])
                # Capacity cost
                add_to_expression!(annual_cost,
                    vars.bat_tech_capacity_investment[y_idx][bt][n],
                    btech.invest_cost_capacity[n])
            end
        end

        # Transmission investment costs
        for ((i, j), var) in vars.transfer_investment[y_idx]
            trans_cost = input.network.transference_invest_cost[i]
            add_to_expression!(annual_cost, var, trans_cost)
        end

        # Reservoir investment costs
        for g in 1:n_gen
            gen = input.generators[g]
            if haskey(vars.reservoir_investment[y_idx], g)
                for n in 1:n_buses
                    if gen.reservoir_invest_max[n] > 0
                        add_to_expression!(annual_cost,
                            vars.reservoir_investment[y_idx][g][n],
                            gen.reservoir_invest_cost[n])
                    end
                end
            end
        end

        # Budget constraint with slack
        @constraint(model,
            annual_cost <= input.max_annual_investment + vars.slack_budget[y_idx],
            base_name = "budget_y$(y_idx)"
        )
    end
end

"""
    add_retirement_cascade_constraints!(model, vars, input)

Add cascade constraints ensuring life extension capacity can only decrease over time.
Once a unit begins retirement, it cannot reverse the decision in later years.
"""
function add_retirement_cascade_constraints!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput
)
    num_years = length(input.years)
    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_buses = input.network.num_buses

    for y_idx in 2:num_years
        # Generator life extension cascade: can only decrease over time
        for g in 1:n_gen
            for n in 1:n_buses
                curr = vars.gen_life_extension[y_idx][g][n]
                prev = vars.gen_life_extension[y_idx - 1][g][n]
                if curr !== nothing && prev !== nothing
                    @constraint(model, curr <= prev,
                        base_name = "cascade_gen_g$(g)_n$(n)_y$(y_idx)")
                end
            end
        end

        # Battery life extension cascade
        for bi in 1:n_bat
            for n in 1:n_buses
                curr = vars.bat_life_extension[y_idx][bi][n]
                prev = vars.bat_life_extension[y_idx - 1][bi][n]
                if curr !== nothing && prev !== nothing
                    @constraint(model, curr <= prev,
                        base_name = "cascade_bat_b$(bi)_n$(n)_y$(y_idx)")
                end
            end
        end
    end
end

"""
    add_re_target_constraints!(model, vars, input, targets)

Add renewable energy penetration target constraints.

CRITICAL: Python legacy (line 1204) uses EQUALITY constraint:
  re_penetration_ratio[y] == target

This FORCES the exact target, not a minimum with slack.
"""
function add_re_target_constraints!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput,
    targets::Dict{Tuple{Int,Int}, Float64}
)
    num_years = length(input.years)
    n_sys = max(1, length(input.system_node_ranges))

    for s_idx in 1:n_sys
        for y_idx in 1:num_years
            target = targets[(s_idx, y_idx)]
            # Use >= with slack to allow falling short (penalized in objective)
            # Equality was infeasible: daily RE availability varies by day
            @constraint(model,
                vars.re_penetration_ratio[(s_idx, y_idx)] + vars.slack_re_target[y_idx] >= target,
                base_name = "re_target_s$(s_idx)_y$(y_idx)"
            )
        end
    end
end

"""
    add_re_increment_constraints!(model, vars, input)

Add annual RE penetration increment constraints.

Matches Python legacy (lines 346-358):
  increment = re_ratio[y] - re_ratio[y-1]
  model += increment >= min_increment
  model += increment <= max_increment

This ensures that the annual change in RE penetration stays within bounds.
"""
function add_re_increment_constraints!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput
)
    num_years = length(input.years)

    # Skip if min/max increments allow any change
    if input.min_re_increment <= 0 && input.max_re_increment >= 1.0
        return
    end

    n_sys = max(1, length(input.system_node_ranges))

    for s_idx in 1:n_sys
        # Initial RE penetration (year-0 reference) for the increment
        # constraint at y_idx=1.  Without anchoring the first year the
        # master can jump from initial_re to anything between [0, 1]
        # without violating the year-on-year increment bound, defeating
        # the purpose of the ``max_annual_increment`` parameter.
        sys_initial_re = if isempty(input.system_node_ranges)
            input.initial_re_penetration
        else
            input.system_node_ranges[s_idx].initial_re
        end

        for y_idx in 1:num_years
            if y_idx == 1
                increment = vars.re_penetration_ratio[(s_idx, y_idx)] - sys_initial_re
            else
                increment = vars.re_penetration_ratio[(s_idx, y_idx)] - vars.re_penetration_ratio[(s_idx, y_idx - 1)]
            end

            if input.min_re_increment > 0
                @constraint(model,
                    increment >= input.min_re_increment,
                    base_name = "re_min_increment_s$(s_idx)_y$(y_idx)"
                )
            end

            if input.max_re_increment < 1.0
                @constraint(model,
                    increment <= input.max_re_increment,
                    base_name = "re_max_increment_s$(s_idx)_y$(y_idx)"
                )
            end
        end
    end
end

"""
    add_capacity_adequacy_constraints!(model, vars, input)

Add capacity adequacy constraints ensuring total capacity meets peak demand.

This is a simplified planning constraint that requires:
  total_generation_capacity + total_battery_power >= peak_demand * reserve_margin

With a slack variable to allow infeasibility at a penalty cost.
"""
function add_capacity_adequacy_constraints!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput
)
    num_years = length(input.years)
    n_buses = input.network.num_buses
    n_gen = length(input.generators)
    n_bat = length(input.batteries)

    # Reserve margin (require extra capacity above peak, configurable)
    reserve_margin = input.reserve_margin

    # Calculate peak demand for each year at each node
    for y_idx in 1:num_years
        # Get year-specific demand slice (with demand growth)
        growth_factor = (1.0 + input.demand_growth)^(y_idx - 1)
        timesteps_per_year_y = input.hours_per_year[y_idx] ÷ input.temporal_resolution_hours
        year_start = (y_idx - 1) * timesteps_per_year_y + 1
        year_end = min(y_idx * timesteps_per_year_y, size(input.base_demand, 1))

        # Capacity adequacy per NODE (aggregate all buses on same node)
        # Generators on different buses of the same node can serve demand
        # on any bus via internal transmission lines
        for ni in 1:input.network.num_nodes
            # Peak demand for this node
            if size(input.base_demand, 2) >= ni && year_end >= year_start
                peak_demand = maximum(input.base_demand[year_start:year_end, ni]) * growth_factor * reserve_margin
            elseif size(input.base_demand, 2) >= ni
                peak_demand = maximum(input.base_demand[:, ni]) * growth_factor * reserve_margin
            else
                peak_demand = maximum(input.base_demand) * growth_factor * reserve_margin
            end

            # Build cumulative capacity expression across ALL buses on this node
            total_capacity = AffExpr(0.0)

            for n in 1:n_buses
                # Skip buses not on this node
                input.network.bus_to_node[n] != ni && continue

                # Generator capacity: unified retirement with degradation
                # For renewables, derate by capacity credit (minimum availability)
                # to avoid counting solar at full rated when it produces 0 at night
                for g in 1:n_gen
                    gen = input.generators[g]
                    rated_power = gen.rated_power[n]
                    lifetime = gen.life_time[n]
                    initial_age = gen.initial_age[n]
                    age_at_year = initial_age + y_idx - 1
                    degradation_rate = gen.degradation_rate[n]

                    # Capacity credit: for renewables, use minimum availability
                    # Solar: 0.0 at night → no firm capacity contribution
                    # Wind: low but >0 minimum → small firm contribution
                    # Dispatchable: 1.0 (always available)
                    is_renewable = gen.type == "Renewable"
                    if is_renewable && size(gen.availability, 1) > 0 && n <= size(gen.availability, 2)
                        capacity_credit = minimum(gen.availability[:, n])
                    else
                        capacity_credit = 1.0
                    end

                    # Risk coefficient: geographic hazard derating (default 1.0)
                    risk_coef = n <= length(gen.risk_coefficient) ? gen.risk_coefficient[n] : 1.0

                    # Existing: active if age < lifetime, with degradation
                    if age_at_year < lifetime && rated_power > 0
                        deg_factor = (1.0 - degradation_rate) ^ age_at_year
                        add_to_expression!(total_capacity, rated_power * deg_factor * capacity_credit * risk_coef)
                    elseif rated_power > 0
                        # Past lifetime: capacity from life extension variable (if any)
                        life_ext_var = vars.gen_life_extension[y_idx][g][n]
                        if life_ext_var !== nothing
                            add_to_expression!(total_capacity, life_ext_var * capacity_credit)
                        end
                    end

                end

                # Battery power capacity: unified retirement (existing only)
                for b in 1:n_bat
                    bat = input.batteries[b]
                    base_power = bat.max_discharge_power[n]
                    lifetime = bat.life_time[n]
                    initial_age = bat.initial_age[n]
                    age_at_year = initial_age + y_idx - 1

                    bat_risk = n <= length(bat.risk_coefficient) ? bat.risk_coefficient[n] : 1.0

                    # Existing: active if age < lifetime
                    if age_at_year < lifetime && base_power > 0
                        add_to_expression!(total_capacity, base_power * bat_risk)
                    elseif base_power > 0
                        life_ext_var = vars.bat_life_extension[y_idx][b][n]
                        if life_ext_var !== nothing
                            add_to_expression!(total_capacity, life_ext_var)
                        end
                    end
                end

                # Technology investments: cumulative capacity with degradation
                for t in 1:length(input.technologies)
                    tech = input.technologies[t]
                    tech_lifetime = tech.life_time[n]
                    tech_deg = tech.degradation_rate[n]
                    # Capacity credit: minimum availability (same logic as existing generators)
                    if tech.type == "Renewable" && size(tech.availability, 1) > 0 && n <= size(tech.availability, 2)
                        tech_cc = minimum(tech.availability[:, n])
                    else
                        tech_cc = 1.0
                    end
                    tech_risk = n <= length(tech.risk_coefficient) ? tech.risk_coefficient[n] : 1.0
                    for y in 1:vars.years_per_inv_period:y_idx
                        inv_age = y_idx - y
                        if inv_age < tech_lifetime
                            deg_factor = (1.0 - tech_deg) ^ inv_age
                            add_to_expression!(total_capacity, vars.tech_investment[y][t][n], deg_factor * tech_cc * tech_risk)
                        end
                    end
                end

                # Battery technology investments: cumulative power
                for bt in 1:length(input.battery_technologies)
                    btech = input.battery_technologies[bt]
                    bt_lifetime = btech.life_time[n]
                    btech_risk = n <= length(btech.risk_coefficient) ? btech.risk_coefficient[n] : 1.0
                    for y in 1:vars.years_per_inv_period:y_idx
                        inv_age = y_idx - y
                        if inv_age < bt_lifetime
                            add_to_expression!(total_capacity, vars.bat_tech_power_investment[y][bt][n], btech_risk)
                        end
                    end
                end
            end

            # Capacity adequacy constraint: capacity + slack >= peak_demand
            @constraint(model,
                total_capacity + vars.slack_capacity[(y_idx, ni)] >= peak_demand,
                base_name = "cap_adequacy_y$(y_idx)_node$(ni)"
            )
        end
    end
end

# =============================================================================
# Cumulative Capacity Calculation
# =============================================================================

"""
    build_cumulative_capacity_expressions(vars, input, year_idx)

Build expressions for cumulative capacity up to and including year_idx.

Returns Dict with:
- "gen" => Dict{gen_idx, Vector{AffExpr}} per node
- "bat_power" => Dict{bat_idx, Vector{AffExpr}} per node
- "bat_capacity" => Dict{bat_idx, Vector{AffExpr}} per node
"""
function build_cumulative_capacity_expressions(
    vars::MasterProblemVariables,
    input::MasterProblemInput,
    year_idx::Int
)::Dict{String, Any}
    n_buses = input.network.num_buses
    b2n = input.network.bus_to_node
    n_gen = length(input.generators)
    n_bat = length(input.batteries)

    result = Dict{String, Any}()

    # Generator capacity: unified retirement with degradation
    gen_cap = Dict{Int, Vector{AffExpr}}()
    for g in 1:n_gen
        gen = input.generators[g]
        gen_cap[g] = Vector{AffExpr}(undef, n_buses)

        for n in 1:n_buses
            rated_power = gen.rated_power[n]
            lifetime = gen.life_time[n]
            initial_age = gen.initial_age[n]
            age_at_year = initial_age + (year_idx - 1)
            degradation_rate = gen.degradation_rate[n]

            cap = AffExpr(0.0)

            # Existing: active if age < lifetime, with degradation
            if age_at_year < lifetime && rated_power > 0
                deg_factor = (1.0 - degradation_rate) ^ age_at_year
                add_to_expression!(cap, rated_power * deg_factor)
            elseif rated_power > 0
                # Past lifetime: capacity from life extension variable (if any)
                life_ext_var = vars.gen_life_extension[year_idx][g][n]
                if life_ext_var !== nothing
                    add_to_expression!(cap, life_ext_var)
                end
            end

            gen_cap[g][n] = cap
        end
    end
    result["gen"] = gen_cap

    # Battery power capacity: unified retirement
    bat_pow = Dict{Int, Vector{AffExpr}}()
    for b in 1:n_bat
        bat = input.batteries[b]
        bat_pow[b] = Vector{AffExpr}(undef, n_buses)

        for n in 1:n_buses
            base_power = bat.max_discharge_power[n]
            lifetime = bat.life_time[n]
            initial_age = bat.initial_age[n]
            age_at_year = initial_age + (year_idx - 1)

            cap = AffExpr(0.0)
            if age_at_year < lifetime && base_power > 0
                add_to_expression!(cap, base_power)
            elseif base_power > 0
                life_ext_var = vars.bat_life_extension[year_idx][b][n]
                if life_ext_var !== nothing
                    add_to_expression!(cap, life_ext_var)
                end
            end
            bat_pow[b][n] = cap
        end
    end
    result["bat_power"] = bat_pow

    # Battery energy capacity: unified retirement
    bat_cap = Dict{Int, Vector{AffExpr}}()
    for b in 1:n_bat
        bat = input.batteries[b]
        bat_cap[b] = Vector{AffExpr}(undef, n_buses)

        for n in 1:n_buses
            base_cap = bat.capacity[n]
            lifetime = bat.life_time[n]
            initial_age = bat.initial_age[n]
            age_at_year = initial_age + (year_idx - 1)

            cap = AffExpr(0.0)
            if age_at_year < lifetime && base_cap > 0
                add_to_expression!(cap, base_cap)
            elseif base_cap > 0
                life_ext_var = vars.bat_life_extension[year_idx][b][n]
                if life_ext_var !== nothing
                    # Scale energy capacity proportional to power life extension
                    bat_config = input.batteries[b]
                    base_power = bat_config.max_discharge_power[n]
                    if base_power > 0
                        ratio = base_cap / base_power
                        add_to_expression!(cap, life_ext_var, ratio)
                    end
                end
            end
            bat_cap[b][n] = cap
        end
    end
    result["bat_capacity"] = bat_cap

    # Technology capacity expressions (per-technology investments)
    n_tech = length(input.technologies)
    ypp = vars.years_per_inv_period
    tech_cap = Dict{Int, Vector{AffExpr}}()
    for t in 1:n_tech
        tech = input.technologies[t]
        tech_cap[t] = Vector{AffExpr}(undef, n_buses)
        for n in 1:n_buses
            cap = AffExpr(0.0)
            tech_lifetime = tech.life_time[n]
            tech_deg = tech.degradation_rate[n]
            for y in 1:ypp:year_idx
                inv_age = year_idx - y
                if inv_age < tech_lifetime
                    deg_factor = (1.0 - tech_deg) ^ inv_age
                    add_to_expression!(cap, vars.tech_investment[y][t][n], deg_factor)
                end
            end
            tech_cap[t][n] = cap
        end
    end
    result["tech"] = tech_cap

    # Battery technology power and capacity expressions
    n_bat_tech = length(input.battery_technologies)
    bat_tech_pow = Dict{Int, Vector{AffExpr}}()
    bat_tech_cap = Dict{Int, Vector{AffExpr}}()
    for bt in 1:n_bat_tech
        btech = input.battery_technologies[bt]
        bat_tech_pow[bt] = Vector{AffExpr}(undef, n_buses)
        bat_tech_cap[bt] = Vector{AffExpr}(undef, n_buses)
        for n in 1:n_buses
            pow = AffExpr(0.0)
            ecap = AffExpr(0.0)
            bt_lifetime = btech.life_time[n]
            for y in 1:ypp:year_idx
                inv_age = year_idx - y
                if inv_age < bt_lifetime
                    add_to_expression!(pow, vars.bat_tech_power_investment[y][bt][n])
                    add_to_expression!(ecap, vars.bat_tech_capacity_investment[y][bt][n])
                end
            end
            bat_tech_pow[bt][n] = pow
            bat_tech_cap[bt][n] = ecap
        end
    end
    result["bat_tech_power"] = bat_tech_pow
    result["bat_tech_capacity"] = bat_tech_cap

    return result
end

# =============================================================================
# Objective Function
# =============================================================================

"""
    build_investment_cost_expression(vars, input)

Build the NPV investment cost expression (first-stage costs only).
Returns an AffExpr containing discounted investment costs at period starts.
"""
function build_investment_cost_expression(
    vars::MasterProblemVariables,
    input::MasterProblemInput
)::AffExpr
    num_years = length(input.years)
    n_buses = input.network.num_buses
    n_gen = length(input.generators)

    inv_cost = AffExpr(0.0)

    ypp = vars.years_per_inv_period
    for y_idx in 1:ypp:num_years
        discount_factor = 1.0 / ((1.0 + input.discount_rate)^(y_idx - 1))

        investment_cost = AffExpr(0.0)

        # Technology investment
        for t in 1:length(input.technologies)
            tech = input.technologies[t]
            for n in 1:n_buses
                add_to_expression!(investment_cost,
                    vars.tech_investment[y_idx][t][n],
                    tech.invest_cost[n])
            end
        end

        # Battery technology investment
        for bt in 1:length(input.battery_technologies)
            btech = input.battery_technologies[bt]
            for n in 1:n_buses
                add_to_expression!(investment_cost,
                    vars.bat_tech_power_investment[y_idx][bt][n],
                    btech.invest_cost_power[n])
                add_to_expression!(investment_cost,
                    vars.bat_tech_capacity_investment[y_idx][bt][n],
                    btech.invest_cost_capacity[n])
            end
        end

        # Transmission investment
        for ((i, j), var) in vars.transfer_investment[y_idx]
            trans_cost = input.network.transference_invest_cost[i]
            add_to_expression!(investment_cost, var, trans_cost)
        end

        # Primary energy infrastructure investment (M10)
        for pe_config in input.pe_configs
            fuel_id = pe_config.fuel_id
            if haskey(vars.fuel_storage_investment, fuel_id) &&
               haskey(vars.fuel_storage_investment[fuel_id], y_idx)
                for n in 1:n_buses
                    add_to_expression!(investment_cost,
                        vars.fuel_storage_investment[fuel_id][y_idx][n],
                        pe_config.storage_invest_cost[n])
                end
            end
            if haskey(vars.fuel_transport_investment, fuel_id) &&
               haskey(vars.fuel_transport_investment[fuel_id], y_idx)
                for (r, var) in vars.fuel_transport_investment[fuel_id][y_idx]
                    route = input.transport_routes[r]
                    transport_cost = pe_config.transport_invest_cost * route.distance_km
                    add_to_expression!(investment_cost, var, transport_cost)
                end
            end
        end

        # Reservoir capacity investment
        for g in 1:n_gen
            gen = input.generators[g]
            if haskey(vars.reservoir_investment[y_idx], g)
                for n in 1:n_buses
                    if gen.reservoir_invest_max[n] > 0
                        add_to_expression!(investment_cost,
                            vars.reservoir_investment[y_idx][g][n],
                            gen.reservoir_invest_cost[n])
                    end
                end
            end
        end

        add_to_expression!(inv_cost, investment_cost, discount_factor)
    end

    return inv_cost
end

"""
    build_master_objective!(model, vars, input)

Build the objective function: NPV of investment + operational costs.
"""
function build_master_objective!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput
)
    num_years = length(input.years)
    n_buses = input.network.num_buses
    b2n = input.network.bus_to_node
    n_gen = length(input.generators)
    n_bat = length(input.batteries)

    total_cost = build_investment_cost_expression(vars, input)

    # Per-year costs: life extension, decommissioning, operational (every year, not just period starts)
    for y_idx in 1:num_years
        discount_factor = 1.0 / ((1.0 + input.discount_rate)^(y_idx - 1))
        annual_cost = AffExpr(0.0)

        # Life extension costs (annual): cost to keep units past lifetime
        for g in 1:n_gen
            gen = input.generators[g]
            for n in 1:n_buses
                life_ext_var = vars.gen_life_extension[y_idx][g][n]
                if life_ext_var !== nothing
                    ext_cost = gen.invest_cost[n] * input.life_extension_cost_factor
                    add_to_expression!(annual_cost, life_ext_var, ext_cost)
                end
            end
        end
        for b in 1:n_bat
            bat = input.batteries[b]
            for n in 1:n_buses
                life_ext_var = vars.bat_life_extension[y_idx][b][n]
                if life_ext_var !== nothing
                    ext_cost = bat.invest_cost_power[n] * input.life_extension_cost_factor
                    add_to_expression!(annual_cost, life_ext_var, ext_cost)
                end
            end
        end

        # Decommissioning costs (incremental, LP-linear)
        for g in 1:n_gen
            gen = input.generators[g]
            for n in 1:n_buses
                rated_power = gen.rated_power[n]
                decomm_cost = gen.decommissioning_cost[n]
                if rated_power <= 0 || decomm_cost <= 0
                    continue
                end
                lifetime = gen.life_time[n]
                initial_age = gen.initial_age[n]
                # Year index when retirement first occurs
                retirement_y = max(1, ceil(Int, lifetime - initial_age + 1))

                if y_idx == retirement_y
                    # First retirement year: decommission full capacity minus life ext
                    age = initial_age + y_idx - 1
                    deg = (1.0 - gen.degradation_rate[n]) ^ age
                    full_cap = rated_power * deg
                    add_to_expression!(annual_cost, full_cap * decomm_cost)
                    lev = vars.gen_life_extension[y_idx][g][n]
                    if lev !== nothing
                        add_to_expression!(annual_cost, lev, -decomm_cost)
                    end
                elseif y_idx > retirement_y
                    # Subsequent years: incremental decommissioning of life-extended capacity
                    prev = vars.gen_life_extension[y_idx - 1][g][n]
                    curr = vars.gen_life_extension[y_idx][g][n]
                    if prev !== nothing
                        add_to_expression!(annual_cost, prev, decomm_cost)
                        if curr !== nothing
                            add_to_expression!(annual_cost, curr, -decomm_cost)
                        end
                    end
                end
            end
        end
        # Battery decommissioning
        for b in 1:n_bat
            bat = input.batteries[b]
            for n in 1:n_buses
                base_power = bat.max_discharge_power[n]
                decomm_cost = bat.decommissioning_cost[n]
                if base_power <= 0 || decomm_cost <= 0
                    continue
                end
                lifetime = bat.life_time[n]
                initial_age = bat.initial_age[n]
                retirement_y = max(1, ceil(Int, lifetime - initial_age + 1))

                if y_idx == retirement_y
                    add_to_expression!(annual_cost, base_power * decomm_cost)
                    lev = vars.bat_life_extension[y_idx][b][n]
                    if lev !== nothing
                        add_to_expression!(annual_cost, lev, -decomm_cost)
                    end
                elseif y_idx > retirement_y
                    prev = vars.bat_life_extension[y_idx - 1][b][n]
                    curr = vars.bat_life_extension[y_idx][b][n]
                    if prev !== nothing
                        add_to_expression!(annual_cost, prev, decomm_cost)
                        if curr !== nothing
                            add_to_expression!(annual_cost, curr, -decomm_cost)
                        end
                    end
                end
            end
        end

        # Add discounted per-year costs
        add_to_expression!(total_cost, annual_cost, discount_factor)

        # Operational costs (second stage) - added from subproblems
        if haskey(vars.operational_costs, y_idx)
            for day_cost in vars.operational_costs[y_idx]
                add_to_expression!(total_cost, day_cost, discount_factor)
            end
        end
    end

    # Slack penalties
    for y_idx in 1:num_years
        add_to_expression!(total_cost, vars.slack_re_target[y_idx], input.slack_penalty)
        add_to_expression!(total_cost, vars.slack_budget[y_idx], input.slack_penalty)
        for ni in 1:input.network.num_nodes
            add_to_expression!(total_cost, vars.slack_capacity[(y_idx, ni)], input.slack_penalty)
        end
    end

    @objective(model, Min, total_cost)

    return total_cost  # Return for MGA near-optimal constraint
end

# =============================================================================
# Representative Days Operational Validation (CRITICAL for correct RE targets)
# =============================================================================

"""
    create_day_ps_vars!(model, input, year_idx, day_idx, hours)

Create PowerSystemVariables for a representative day.
Uses power_system.jl's PowerSystemVariables type directly instead of a separate struct.
"""
function create_day_ps_vars!(
    model::Model,
    input::MasterProblemInput,
    year_idx::Int,
    day_idx::Int,
    hours::Int
)::PowerSystemVariables
    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_buses = input.network.num_buses
    n_nodes = input.network.num_nodes
    prefix = "op_y$(year_idx)_d$(day_idx)"

    # Pre-compute lookup maps: which buses each generator/battery is active at
    buses_of_gen = [Int[] for _ in 1:n_gen]
    gens_at_bus = [Int[] for _ in 1:n_buses]
    for g in 1:n_gen
        gen = input.generators[g]
        for b in 1:n_buses
            has_cap = gen.rated_power[b] > 0
            has_invest = length(gen.invest_max) >= b && gen.invest_max[b] > 0
            if has_cap || has_invest
                push!(buses_of_gen[g], b)
                push!(gens_at_bus[b], g)
            end
        end
    end

    buses_of_bat = [Int[] for _ in 1:n_bat]
    bats_at_bus = [Int[] for _ in 1:n_buses]
    for bi in 1:n_bat
        bat = input.batteries[bi]
        for b in 1:n_buses
            has_cap = bat.max_discharge_power[b] > 0
            has_invest = length(bat.invest_max_power) >= b && bat.invest_max_power[b] > 0
            if has_cap || has_invest
                push!(buses_of_bat[bi], b)
                push!(bats_at_bus[b], bi)
            end
        end
    end

    # Generator output — SparseAxisArray (only active gen-bus pairs)
    gen_output = @variable(model,
        [g=1:n_gen, b=buses_of_gen[g], t=1:hours],
        lower_bound = 0, base_name = "$(prefix)_gen")

    # gen_status fixed at 1 (economic dispatch — no UC in master)
    gen_status = @variable(model,
        [g=1:n_gen, b=buses_of_gen[g], t=1:hours],
        lower_bound = 1, upper_bound = 1, base_name = "$(prefix)_gs")

    # Curtailment [node, hour] (per-node, matching PowerSystemVariables convention)
    curtailment = @variable(model,
        [n=1:n_nodes, t=1:hours],
        lower_bound = 0, base_name = "$(prefix)_curt")

    # FRE penetration loss [node, hour]
    fre_penetration_loss = @variable(model,
        [n=1:n_nodes, t=1:hours],
        lower_bound = 0, base_name = "$(prefix)_fre_loss")

    # Battery variables — SparseAxisArray (only active bat-bus pairs)
    bat_charge = @variable(model,
        [bi=1:n_bat, b=buses_of_bat[bi], t=1:hours],
        lower_bound = 0, base_name = "$(prefix)_bat_ch")

    bat_discharge = @variable(model,
        [bi=1:n_bat, b=buses_of_bat[bi], t=1:hours],
        lower_bound = 0, base_name = "$(prefix)_bat_dch")

    # bat_soc uses hours+1 convention (soc[1]=initial, soc[t+1]=after hour t)
    bat_soc = @variable(model,
        [bi=1:n_bat, b=buses_of_bat[bi], t=1:(hours+1)],
        lower_bound = 0, base_name = "$(prefix)_bat_soc")

    # Voltage angle + power flow by line (DC power flow for multi-bus)
    voltage_angle = @variable(model,
        [b=1:n_buses, t=1:hours],
        lower_bound = -π, upper_bound = π,
        base_name = "$(prefix)_vangle")

    # Power flow dict (legacy node-pair indexing, populated by add_dc_constraints!)
    power_flow = Dict{Tuple{Int,Int}, Vector{VariableRef}}()

    # Reserve variables [node, hour]
    reserve_static = @variable(model,
        [n=1:n_nodes, t=1:hours],
        lower_bound = 0, base_name = "$(prefix)_rs")
    reserve_dynamic = @variable(model,
        [n=1:n_nodes, t=1:hours],
        lower_bound = 0, base_name = "$(prefix)_rd")
    reserve_static_loss = @variable(model,
        [n=1:n_nodes, t=1:hours],
        lower_bound = 0, base_name = "$(prefix)_rs_loss")
    reserve_dynamic_loss = @variable(model,
        [n=1:n_nodes, t=1:hours],
        lower_bound = 0, base_name = "$(prefix)_rd_loss")

    # Load shedding [bus, hour] (B2: indexed per-bus to match operational layout).
    # In the master, ``buses == nodes`` (one synthetic bus per node) so n_buses == n_nodes.
    load_shed = @variable(model,
        [b=1:n_buses, t=1:hours],
        lower_bound = 0, base_name = "$(prefix)_ll")

    # Inertia loss variable (only if inertia limit is configured)
    loss_of_inertia_var = if input.inertia_limit > 0
        @variable(model, [t=1:hours], lower_bound=0,
            base_name = "$(prefix)_inertia_loss")
    else
        nothing
    end

    # Sectoral loss of load variables (M2)
    loss_of_load_sectoral = Dict{String, Matrix{VariableRef}}()
    if !isempty(input.sectoral_criticality)
        for (sector, _) in input.sectoral_criticality
            loss_of_load_sectoral[sector] = @variable(model,
                [b=1:n_buses, t=1:hours],
                lower_bound = 0, base_name = "$(prefix)_lol_$(sector)")
        end
    end

    # Segment variables for PWL cost decomposition (bidding curves)
    gen_seg_output = Dict{Tuple{Int,Int}, Any}()
    for (g, bus_curves) in input.gen_cost_curves
        for (b, segs) in bus_curves
            if length(segs) > 1 && b in buses_of_gen[g]
                n_seg = length(segs)
                sv = @variable(model, [k=1:n_seg, t=1:hours],
                               lower_bound=0, base_name="$(prefix)_gseg_$(g)_$(b)")
                gen_seg_output[(g, b)] = sv
                for t in 1:hours
                    @constraint(model, gen_output[g, b, t] ==
                        sum(sv[k, t] for k in 1:n_seg))
                end
            end
        end
    end

    bat_seg_discharge = Dict{Tuple{Int,Int}, Any}()
    for (bi, bus_curves) in input.bat_cost_curves
        for (b, segs) in bus_curves
            if length(segs) > 1 && b in buses_of_bat[bi]
                n_seg = length(segs)
                sv = @variable(model, [k=1:n_seg, t=1:hours],
                               lower_bound=0, base_name="$(prefix)_bseg_$(bi)_$(b)")
                bat_seg_discharge[(bi, b)] = sv
                for t in 1:hours
                    @constraint(model, bat_discharge[bi, b, t] ==
                        sum(sv[k, t] for k in 1:n_seg))
                end
            end
        end
    end

    # Reservoir hydro variables for the representative day. Only created when at
    # least one generator has a reservoir, so all-thermal/RE systems are not
    # bloated. add_reservoir_constraints! enforces the water balance + cyclic
    # level so hydro is energy-limited in the master (not treated as firm MW).
    has_any_reservoir = any(any(g.reservoir_capacity .> 0) for g in input.generators)
    reservoir_level = nothing
    reservoir_spillage = nothing
    reservoir_pump = nothing
    if has_any_reservoir
        reservoir_level = @variable(model,
            [g=1:n_gen, b=buses_of_gen[g], t=1:(hours+1)],
            lower_bound = 0, base_name = "$(prefix)_res_level")
        reservoir_spillage = @variable(model,
            [g=1:n_gen, b=buses_of_gen[g], t=1:hours],
            lower_bound = 0, base_name = "$(prefix)_res_spill")
        reservoir_pump = @variable(model,
            [g=1:n_gen, b=buses_of_gen[g], t=1:hours],
            lower_bound = 0, base_name = "$(prefix)_res_pump")
    end

    return PowerSystemVariables(
        gen_output, gen_status, nothing, nothing,  # no startup/shutdown
        curtailment, fre_penetration_loss,
        bat_charge, bat_discharge, bat_soc,
        power_flow, voltage_angle,
        reserve_static, reserve_dynamic, reserve_static_loss, reserve_dynamic_loss,
        load_shed,
        nothing, nothing, nothing, nothing,  # no investment vars
        buses_of_gen, gens_at_bus, buses_of_bat, bats_at_bus;
        loss_of_inertia = loss_of_inertia_var,
        loss_of_load_sectoral = loss_of_load_sectoral,
        gen_seg_output = gen_seg_output,
        bat_seg_discharge = bat_seg_discharge,
        reservoir_level = reservoir_level,
        reservoir_spillage = reservoir_spillage,
        reservoir_pump = reservoir_pump
    )
end

"""
    add_day_operational_constraints!(model, ps_vars, vars, input, year_idx, day_idx, demand, start_hour)

Add operational constraints for a representative day by delegating to power_system.jl
and transmission_dc.jl functions. Only master-specific logic (capacity precomputation,
technology dispatch, RE enforcement, sectoral LoL) remains here.

CRITICAL: This links investment decisions to operational feasibility.
"""
function add_day_operational_constraints!(
    model::Model,
    ps_vars::PowerSystemVariables,
    vars::MasterProblemVariables,
    input::MasterProblemInput,
    year_idx::Int,
    day_idx::Int,
    demand::Matrix{Float64},
    start_hour::Int;
    # TSAM inter-period SOC linking overrides (optional)
    initial_soc_overrides::Union{Nothing, Dict{Tuple{Int,Int}, Any}} = nothing,
    final_soc_targets::Union{Nothing, Dict{Tuple{Int,Int}, Any}} = nothing,
    # TSAM inter-period reservoir-level linking overrides (optional, seasonal hydro)
    initial_reservoir_overrides::Union{Nothing, Dict{Tuple{Int,Int}, Any}} = nothing,
    final_reservoir_targets::Union{Nothing, Dict{Tuple{Int,Int}, Any}} = nothing,
    # Inter-system DC-OPF: external flow contributions at border buses
    # (bus, hour) => AffExpr to add to flow_sum in KCL
    external_injections::Union{Nothing, Dict{Tuple{Int,Int}, AffExpr}} = nothing
)
    hours = size(demand, 1)
    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_buses = input.network.num_buses
    b2n = input.network.bus_to_node

    # Apply demand growth
    growth_factor = (1.0 + input.demand_growth)^(year_idx - 1)

    # =========================================================================
    # Precompute total capacity per generator/battery per bus for this year
    # These are AffExpr (may contain investment variables) passed to power_system.jl
    # =========================================================================
    total_cap_gen = Dict{Tuple{Int,Int}, Any}()
    for g in 1:n_gen
        gen = input.generators[g]
        for b in ps_vars.buses_of_gen[g]
            cap = AffExpr(0.0)
            rated_power = gen.rated_power[b]
            lifetime = gen.life_time[b]
            initial_age = gen.initial_age[b]
            age_at_year = initial_age + (year_idx - 1)
            degradation_rate = gen.degradation_rate[b]

            if age_at_year < lifetime && rated_power > 0
                deg_factor = (1.0 - degradation_rate) ^ age_at_year
                add_to_expression!(cap, rated_power * deg_factor)
            elseif rated_power > 0
                life_ext_var = vars.gen_life_extension[year_idx][g][b]
                if life_ext_var !== nothing
                    add_to_expression!(cap, life_ext_var)
                end
            end

            total_cap_gen[(g, b)] = cap
        end
    end

    total_cap_bat_power = Dict{Tuple{Int,Int}, Any}()
    total_cap_bat_energy = Dict{Tuple{Int,Int}, Any}()
    for bi in 1:n_bat
        bat = input.batteries[bi]
        for b in ps_vars.buses_of_bat[bi]
            pow_cap = AffExpr(0.0)
            base_power = bat.max_discharge_power[b]
            lifetime = bat.life_time[b]
            initial_age = bat.initial_age[b]
            age_at_year = initial_age + (year_idx - 1)

            if age_at_year < lifetime && base_power > 0
                add_to_expression!(pow_cap, base_power)
            elseif base_power > 0
                life_ext_var = vars.bat_life_extension[year_idx][bi][b]
                if life_ext_var !== nothing
                    add_to_expression!(pow_cap, life_ext_var)
                end
            end
            total_cap_bat_power[(bi, b)] = pow_cap

            en_cap = AffExpr(0.0)
            base_cap = bat.capacity[b]
            if age_at_year < lifetime && base_cap > 0
                add_to_expression!(en_cap, base_cap)
            elseif base_cap > 0 && base_power > 0
                life_ext_var = vars.bat_life_extension[year_idx][bi][b]
                if life_ext_var !== nothing
                    ratio = base_cap / base_power
                    add_to_expression!(en_cap, life_ext_var, ratio)
                end
            end
            total_cap_bat_energy[(bi, b)] = en_cap
        end
    end

    # =========================================================================
    # Technology Generation Variables (per-technology investment dispatch)
    # Technologies are master-only concepts — not in power_system.jl
    # =========================================================================
    n_tech = length(input.technologies)
    n_bat_tech = length(input.battery_technologies)

    tech_output = nothing
    if n_tech > 0
        tech_output = @variable(model,
            [t=1:n_tech, b=1:n_buses, ts=1:hours],
            lower_bound = 0,
            base_name = "tech_out_y$(year_idx)_d$(day_idx)")

        for t in 1:n_tech
            tech = input.technologies[t]
            is_renewable = tech.type == "Renewable"
            for b in 1:n_buses
                cumul_cap = AffExpr(0.0)
                tech_lifetime = tech.life_time[b]
                tech_deg = tech.degradation_rate[b]
                for y in 1:vars.years_per_inv_period:year_idx
                    inv_age = year_idx - y
                    if inv_age < tech_lifetime
                        deg = (1.0 - tech_deg) ^ inv_age
                        add_to_expression!(cumul_cap, vars.tech_investment[y][t][b], deg)
                    end
                end
                for ts in 1:hours
                    actual_hour = start_hour + ts - 1
                    avail_hours = size(tech.availability, 1)
                    avail_hour = avail_hours > 0 ? mod1(actual_hour, avail_hours) : 1
                    avail = is_renewable ? tech.availability[avail_hour, b] : 1.0
                    @constraint(model,
                        tech_output[t, b, ts] <= cumul_cap * avail,
                        base_name = "tech_cap_t$(t)_b$(b)_ts$(ts)_y$(year_idx)_d$(day_idx)")
                end
            end
        end
    end

    bat_tech_discharge = nothing
    bat_tech_charge = nothing
    bat_tech_soc = nothing
    if n_bat_tech > 0
        bat_tech_discharge = @variable(model,
            [bt=1:n_bat_tech, b=1:n_buses, ts=1:hours],
            lower_bound = 0, base_name = "bat_tech_dis_y$(year_idx)_d$(day_idx)")
        bat_tech_charge = @variable(model,
            [bt=1:n_bat_tech, b=1:n_buses, ts=1:hours],
            lower_bound = 0, base_name = "bat_tech_ch_y$(year_idx)_d$(day_idx)")
        bat_tech_soc = @variable(model,
            [bt=1:n_bat_tech, b=1:n_buses, ts=0:hours],
            lower_bound = 0, base_name = "bat_tech_soc_y$(year_idx)_d$(day_idx)")

        for bt in 1:n_bat_tech
            btech = input.battery_technologies[bt]
            for b in 1:n_buses
                pow_cap = AffExpr(0.0)
                en_cap = AffExpr(0.0)
                bt_lifetime = btech.life_time[b]
                for y in 1:vars.years_per_inv_period:year_idx
                    inv_age = year_idx - y
                    if inv_age < bt_lifetime
                        add_to_expression!(pow_cap, vars.bat_tech_power_investment[y][bt][b])
                        add_to_expression!(en_cap, vars.bat_tech_capacity_investment[y][bt][b])
                    end
                end
                for ts in 1:hours
                    @constraint(model, bat_tech_discharge[bt, b, ts] <= pow_cap,
                        base_name = "bt_dis_cap_bt$(bt)_b$(b)_ts$(ts)_y$(year_idx)_d$(day_idx)")
                    @constraint(model, bat_tech_charge[bt, b, ts] <= pow_cap,
                        base_name = "bt_ch_cap_bt$(bt)_b$(b)_ts$(ts)_y$(year_idx)_d$(day_idx)")
                end
                for ts in 0:hours
                    @constraint(model, bat_tech_soc[bt, b, ts] <= en_cap,
                        base_name = "bt_soc_cap_bt$(bt)_b$(b)_ts$(ts)_y$(year_idx)_d$(day_idx)")
                end
                ch_eff = btech.charge_efficiency[b]
                dis_eff = btech.discharge_efficiency[b]
                initial_soc_frac = btech.soc_initial[b]
                # Clamp to soc_min to avoid initial/end SOC vs soc_min conflict
                soc_min_b = btech.soc_min[b]
                if initial_soc_frac < soc_min_b
                    initial_soc_frac = soc_min_b
                end
                # Skip SOC dynamics if efficiency is zero (tech not at this node)
                if dis_eff <= 0.0 || ch_eff <= 0.0
                    for ts in 0:hours
                        @constraint(model, bat_tech_soc[bt, b, ts] <= 0,
                            base_name = "bt_soc_zero_bt$(bt)_b$(b)_ts$(ts)_y$(year_idx)_d$(day_idx)")
                    end
                    for ts in 1:hours
                        @constraint(model, bat_tech_discharge[bt, b, ts] <= 0,
                            base_name = "bt_dis_zero_bt$(bt)_b$(b)_ts$(ts)_y$(year_idx)_d$(day_idx)")
                        @constraint(model, bat_tech_charge[bt, b, ts] <= 0,
                            base_name = "bt_ch_zero_bt$(bt)_b$(b)_ts$(ts)_y$(year_idx)_d$(day_idx)")
                    end
                else
                    @constraint(model, bat_tech_soc[bt, b, 0] == en_cap * initial_soc_frac,
                        base_name = "bt_soc_init_bt$(bt)_b$(b)_y$(year_idx)_d$(day_idx)")
                    for ts in 1:hours
                        @constraint(model,
                            bat_tech_soc[bt, b, ts] == bat_tech_soc[bt, b, ts-1]
                                + ch_eff * bat_tech_charge[bt, b, ts]
                                - (1.0/dis_eff) * bat_tech_discharge[bt, b, ts],
                            base_name = "bt_soc_dyn_bt$(bt)_b$(b)_ts$(ts)_y$(year_idx)_d$(day_idx)")
                    end
                    @constraint(model, bat_tech_soc[bt, b, hours] == en_cap * initial_soc_frac,
                        base_name = "bt_soc_cyc_bt$(bt)_b$(b)_y$(year_idx)_d$(day_idx)")
                end
            end
        end
    end

    # =========================================================================
    # Build day input NamedTuple for power_system.jl / transmission_dc.jl
    # =========================================================================
    n_nodes = input.network.num_nodes
    demand_slice = Matrix{Float64}(undef, hours, n_nodes)
    for t in 1:hours, n in 1:n_nodes
        demand_slice[t, n] = demand[t, n] * growth_factor
    end

    # Slice generator availability for this day's hours
    # power_system.jl accesses gen.availability[t, b] where t=1..hours
    # so we need availability[1:hours, :] to correspond to this day's actual hours
    day_generators = map(input.generators) do gen
        full_avail = gen.availability
        avail_rows = size(full_avail, 1)
        if avail_rows > 0
            sliced = Matrix{Float64}(undef, hours, size(full_avail, 2))
            for t in 1:hours
                actual_hour = start_hour + t - 1
                h = mod1(actual_hour, avail_rows)
                for b in 1:size(full_avail, 2)
                    sliced[t, b] = full_avail[h, b]
                end
            end
            # Create new GeneratorConfig with sliced availability
            GeneratorConfig(
                gen.name, gen.type, gen.fuel,
                gen.rated_power, gen.min_power,
                gen.efficiency_rated, gen.efficiency_min,
                gen.ramp_up, gen.ramp_down,
                gen.min_up_time, gen.min_down_time,
                gen.start_up_cost, gen.fuel_cost,
                gen.fixed_cost, gen.maintenance_cost,
                gen.inertia, gen.invest_cost, gen.invest_max,
                sliced, gen.reservable,
                gen.life_time, gen.initial_age,
                gen.degradation_rate, gen.decommissioning_cost,
                gen.frequency_hz, gen.current_type,
                gen.reservoir_capacity, gen.reservoir_initial_level,
                gen.reservoir_min_level, gen.reservoir_max_level,
                gen.reservoir_inflow, gen.reservoir_turbine_efficiency,
                gen.reservoir_evaporation_rate, gen.reservoir_pump_capacity,
                gen.reservoir_pump_efficiency, gen.reservoir_spillage_allowed,
                gen.reservoir_invest_cost, gen.reservoir_invest_max,
                gen.risk_coefficient,
                gen.reservoir_min_release,
                gen.cascade_downstream, gen.cascade_delay_hours
            )
        else
            gen
        end
    end

    day_input = (
        generators = day_generators,
        batteries = input.batteries,
        network = input.network,
        demand = demand_slice,
        temporal = (hours = hours, resolution_hours = input.temporal_resolution_hours),
        year = input.base_year + year_idx - 1,
        base_year = input.base_year,
        mode = "economic_dispatch",
        reserve_static_requirement = input.reserve_static_requirement,
        reserve_static_default_ratio = input.reserve_static_default_ratio,
        reserve_dynamic_requirement = input.reserve_dynamic_requirement,
        dynamic_reserve_contribution = input.dynamic_reserve_contribution,
        inertia_limit = input.inertia_limit,
        inertia_limit_hourly = Float64[],
        loss_demand_threshold = 1.0,
        rooftop_generation = nothing,
        re_penetration_target = 0.0,
        soc_end_tolerance = 0.0,
        min_cycling_ratio = 0.0,
        min_cycling_period_days = 1.0,
        pwl_loss_segments = input.transmission_loss_segments,
        gen_cost_curves = input.gen_cost_curves,
        bat_cost_curves = input.bat_cost_curves,
        # Rolling-horizon seam slots are not applicable inside the master's
        # representative-day subproblems (each day is solved fresh, with no
        # seam to a prior day). Provide empty Dicts so the shared
        # power_system.jl constraint builders fall back to their no-boundary
        # paths instead of erroring on a missing field.
        generator_output_prev = Dict{Int, Dict{Int, Float64}}(),
        reservoir_level_prev = Dict{Int, Dict{Int, Float64}}(),
    )

    # =========================================================================
    # DELEGATE to power_system.jl constraint functions
    # =========================================================================
    add_generator_constraints!(model, ps_vars, day_input;
        capacity_override=total_cap_gen)
    add_battery_constraints!(model, ps_vars, day_input;
        capacity_override_power=total_cap_bat_power,
        capacity_override_energy=total_cap_bat_energy,
        initial_soc_overrides=initial_soc_overrides,
        final_soc_targets=final_soc_targets)
    # Reservoir water balance / cyclic level — gives hydro an energy budget in
    # the master (previously omitted, so hydro was over-credited as firm MW).
    # When TSAM seasonal linking is active the cyclic closure is replaced by a
    # chronological chain across periods (overrides below). No-op with no reservoir.
    add_reservoir_constraints!(model, ps_vars, day_input;
        initial_reservoir_overrides=initial_reservoir_overrides,
        final_reservoir_targets=final_reservoir_targets)
    add_reserve_constraints!(model, ps_vars, day_input;
        capacity_override=total_cap_gen,
        demand_scale=1.0)  # demand already scaled in demand_slice
    add_inertia_constraints!(model, ps_vars, day_input)

    # =========================================================================
    # Build extra injection function for balance (master-specific terms)
    # =========================================================================
    extra_fn = (bus, t) -> begin
        expr = AffExpr(0.0)
        # Technology generation
        if tech_output !== nothing
            for tc in 1:n_tech
                add_to_expression!(expr, tech_output[tc, bus, t])
            end
        end
        # Battery technology net discharge
        if bat_tech_discharge !== nothing
            for btc in 1:n_bat_tech
                add_to_expression!(expr, bat_tech_discharge[btc, bus, t])
                add_to_expression!(expr, -1.0, bat_tech_charge[btc, bus, t])
            end
        end
        # Sectoral loss of load contributions
        if ps_vars.loss_of_load_sectoral !== nothing
            for (sector, lol_vars) in ps_vars.loss_of_load_sectoral
                add_to_expression!(expr, lol_vars[bus, t])
            end
        end
        # Inter-system external injections
        if external_injections !== nothing && haskey(external_injections, (bus, t))
            add_to_expression!(expr, external_injections[(bus, t)])
        end
        return expr
    end

    # =========================================================================
    # DELEGATE to transmission_dc.jl / power_system.jl for power balance
    # =========================================================================
    # Use network.transmission_lines (populated by convert_network_config from YAML)
    # NOT input.transmission_lines (MasterProblemInput field, may be empty)
    network_lines = input.network.transmission_lines
    n_lines = length(network_lines)
    if n_buses > 1 && n_lines > 0
        transmission = TransmissionDC(input.network)
        add_dc_constraints!(model, transmission, ps_vars, day_input;
            extra_injections_fn=extra_fn)

        # Build line capacity overrides with cumulative investment
        # Use transmission.lines (from TransmissionDC) which includes transformers
        line_cap_override = Dict{Int, Any}()
        for (l, (from, to)) in enumerate(transmission.lines)
            base_cap = transmission.line_capacities[l]
            inv_cap = AffExpr(0.0)
            for y in 1:vars.years_per_inv_period:year_idx
                if haskey(vars.transfer_investment[y], (from, to))
                    add_to_expression!(inv_cap, vars.transfer_investment[y][(from, to)])
                end
            end
            line_cap_override[l] = base_cap + inv_cap
        end
        add_line_capacity_constraints!(model, transmission, ps_vars, day_input;
            capacity_override=line_cap_override)
    else
        add_demand_constraints!(model, ps_vars, day_input;
            extra_injections_fn=extra_fn)
    end

    # =========================================================================
    # DELEGATE curtailment to power_system.jl
    # =========================================================================
    add_curtailment_constraints!(model, ps_vars, day_input;
        capacity_override=total_cap_gen)

    # =========================================================================
    # Sectoral Loss-of-Load Upper Bounds (M2) — master-specific
    # =========================================================================
    if ps_vars.loss_of_load_sectoral !== nothing && !isempty(ps_vars.loss_of_load_sectoral) && !isempty(input.sectoral_demand)
        for (sector, lol_vars) in ps_vars.loss_of_load_sectoral
            if haskey(input.sectoral_demand, sector)
                sec_dem = input.sectoral_demand[sector]
                for t in 1:hours
                    actual_hour = start_hour + t - 1
                    hours_in_data = size(sec_dem, 1)
                    hour_in_year = hours_in_data > 0 ? mod1(actual_hour, hours_in_data) : 1
                    for n in 1:n_buses
                        parent_node = input.network.bus_to_node[n]
                        bus_fraction = input.network.buses[n].demand_fraction
                        sec_demand_val = sec_dem[hour_in_year, parent_node] * bus_fraction * growth_factor
                        @constraint(model,
                            lol_vars[n, t] <= max(0.0, sec_demand_val),
                            base_name = "lol_sec_ub_$(sector)_y$(year_idx)_d$(day_idx)_n$(n)_t$(t)")
                    end
                end
            end
        end
    end

    # =========================================================================
    # CRITICAL: Per-System Renewable Penetration Constraints — master-specific
    # Links to re_penetration_ratio[(s,y)] variable
    # MUST include both existing generators AND technology investments
    # =========================================================================
    targets = calculate_target_ratios(input)

    # Build system ranges (default: single system covering all buses)
    sys_ranges = input.system_node_ranges
    if isempty(sys_ranges)
        sys_ranges = [SystemNodeRange("global", 1, n_buses, input.initial_re_penetration)]
    end

    for (s_idx, sr) in enumerate(sys_ranges)
        sys_first = sr.first_bus
        sys_last = sr.first_bus + sr.num_buses - 1
        sys_buses = Set(sys_first:sys_last)

        sys_renewable = AffExpr(0.0)
        sys_generation = AffExpr(0.0)

        # Existing generators — only count buses in this system
        for g in 1:n_gen
            gen = input.generators[g]
            is_renewable = gen.type == "Renewable"
            for n in ps_vars.buses_of_gen[g]
                n in sys_buses || continue
                for t in 1:hours
                    add_to_expression!(sys_generation, ps_vars.gen_output[g, n, t])
                    if is_renewable
                        add_to_expression!(sys_renewable, ps_vars.gen_output[g, n, t])
                    end
                end
            end
        end

        # Technology investments — only count buses in this system
        if tech_output !== nothing
            for tc in 1:n_tech
                tech = input.technologies[tc]
                is_renewable = tech.type == "Renewable"
                for b in sys_first:sys_last
                    for t in 1:hours
                        add_to_expression!(sys_generation, tech_output[tc, b, t])
                        if is_renewable
                            add_to_expression!(sys_renewable, tech_output[tc, b, t])
                        end
                    end
                end
            end
        end

        target_ratio = targets[(s_idx, year_idx)]

        # System demand for this day (only system's buses)
        sys_demand_day = 0.0
        for t in 1:hours
            actual_hour = start_hour + t - 1
            hours_in_data = size(input.base_demand, 1)
            hour_in_year = hours_in_data > 0 ? mod1(actual_hour, hours_in_data) : 1
            for b in sys_first:sys_last
                parent_node = input.network.bus_to_node[b]
                bus_fraction = input.network.buses[b].demand_fraction
                sys_demand_day += input.base_demand[hour_in_year, parent_node] * bus_fraction
            end
        end
        sys_demand_day *= growth_factor

        # Map bus range to unique nodes for node-level variables
        sys_node_set = unique([input.network.bus_to_node[b] for b in sys_first:sys_last])
        sys_fre_loss = @expression(model,
            sum(ps_vars.fre_penetration_loss[n, t] for n in sys_node_set, t in 1:hours))

        # RE minimum: at least target_ratio of system demand must be renewable
        @constraint(model,
            sys_renewable + sys_fre_loss >= target_ratio * sys_demand_day,
            base_name = "re_min_day_s$(s_idx)_y$(year_idx)_d$(day_idx)")

        # NOTE: No per-day maximum RE constraint. Exceeding the RE target on
        # high-availability days is physically valid and expected. The annual
        # re_target constraint (with slack) ensures the overall ratio is met.

        if sys_demand_day > 0
            # RE ratio lower bound: ratio represents the worst-case daily fraction
            @constraint(model,
                vars.re_penetration_ratio[(s_idx, year_idx)] * sys_demand_day <= sys_renewable + sys_fre_loss,
                base_name = "re_ratio_lb_s$(s_idx)_y$(year_idx)_d$(day_idx)")
            # NOTE: No upper bound on per-day RE ratio. The re_penetration_ratio
            # represents the minimum achieved fraction across all representative
            # days, allowing higher RE on favorable days.
        end
    end

    # =========================================================================
    # Maximum Curtailment Constraint — master-specific
    # Includes BOTH generator curtailment AND technology RE curtailment
    # (available tech capacity not dispatched)
    # =========================================================================
    # Accumulator for tech-investment curtailment cost — added to the master
    # objective so the LP trades tech overbuild against storage capex on an
    # explicit economic basis (the max_curtailment_ratio constraint alone
    # empirically does not bind for the full-year operational LP).
    tech_curt_cost = AffExpr(0.0)

    if input.max_curtailment_ratio < 1.0
        # Build as AffExpr explicitly: when the sum collapses to a single
        # term (n_nodes=1 ∧ hours=1) `@expression` returns a bare
        # VariableRef and later add_to_expression!(VarRef, AffExpr) fails.
        total_curtailment = AffExpr(0.0)
        for n in 1:n_nodes, t in 1:hours
            add_to_expression!(total_curtailment, ps_vars.curtailment[n, t])
        end
        # Compute total renewable generation across all buses
        total_renewable = AffExpr(0.0)
        for g in 1:n_gen
            gen = input.generators[g]
            if gen.type == "Renewable"
                for n in ps_vars.buses_of_gen[g]
                    for t in 1:hours
                        add_to_expression!(total_renewable, ps_vars.gen_output[g, n, t])
                    end
                end
            end
        end
        if tech_output !== nothing
            n_tech_local = length(input.technologies)
            for tc in 1:n_tech_local
                tech = input.technologies[tc]
                if tech.type == "Renewable"
                    for b in 1:n_buses
                        for ts in 1:hours
                            add_to_expression!(total_renewable, tech_output[tc, b, ts])
                        end
                    end
                end
            end
        end
        # Add technology RE curtailment: available - dispatched
        if tech_output !== nothing
            n_tech_local = length(input.technologies)
            for tc in 1:n_tech_local
                tech = input.technologies[tc]
                if tech.type == "Renewable"
                    for b in 1:n_buses
                        # Recompute cumulative capacity (same as tech constraint setup)
                        cumul_cap = AffExpr(0.0)
                        tech_lifetime = tech.life_time[b]
                        tech_deg = tech.degradation_rate[b]
                        for y in 1:vars.years_per_inv_period:year_idx
                            inv_age = year_idx - y
                            if inv_age < tech_lifetime
                                deg = (1.0 - tech_deg) ^ inv_age
                                add_to_expression!(cumul_cap,
                                    vars.tech_investment[y][tc][b], deg)
                            end
                        end
                        for ts in 1:hours
                            actual_hour = start_hour + ts - 1
                            avail_hours = size(tech.availability, 1)
                            avail_hour = avail_hours > 0 ? mod1(actual_hour, avail_hours) : 1
                            avail = tech.availability[avail_hour, b]
                            # tech curtailment = cumul_cap × avail - tech_output
                            add_to_expression!(total_curtailment, cumul_cap * avail)
                            add_to_expression!(total_curtailment, -1.0, tech_output[tc, b, ts])
                            # Penalise the same expression in the objective
                            if input.curtailment_cost > 0
                                add_to_expression!(tech_curt_cost,
                                    cumul_cap * (avail * input.curtailment_cost))
                                add_to_expression!(tech_curt_cost,
                                    -input.curtailment_cost, tech_output[tc, b, ts])
                            end
                        end
                    end
                end
            end
        end
        # Only add constraint if there are renewable terms (avoid forcing curtailment=0)
        if length(total_renewable.terms) > 0
            @constraint(model,
                total_curtailment <= input.max_curtailment_ratio * total_renewable,
                base_name = "max_curtailment_y$(year_idx)_d$(day_idx)")
        end
    end

    # Return tech_output AND the tech curtailment cost (the caller adds the
    # latter to the master objective alongside calculate_day_operational_cost).
    return tech_output, tech_curt_cost
end

"""
    calculate_day_operational_cost(ps_vars, input, year_idx, hours)

Calculate operational cost for a representative day.
"""
function calculate_day_operational_cost(
    ps_vars::PowerSystemVariables,
    input::MasterProblemInput,
    year_idx::Int,
    hours::Int;
    tech_output::Union{Nothing, Any} = nothing
)::AffExpr
    n_gen = length(input.generators)
    n_buses = input.network.num_buses
    n_nodes = input.network.num_nodes

    cost = AffExpr(0.0)

    for t in 1:hours
        for g in 1:n_gen
            gen = input.generators[g]
            for n in ps_vars.buses_of_gen[g]
                fixed_cost = gen.fixed_cost[n]
                maint_cost = gen.maintenance_cost[n]
                if haskey(ps_vars.gen_seg_output, (g, n))
                    # PWL fuel cost via segments
                    segs = input.gen_cost_curves[g][n]
                    seg_vars = ps_vars.gen_seg_output[(g, n)]
                    for k in 1:length(segs)
                        add_to_expression!(cost, segs[k].marginal_cost, seg_vars[k, t])
                    end
                    add_to_expression!(cost, ps_vars.gen_output[g, n, t],
                                      fixed_cost + maint_cost)
                else
                    fuel_cost = gen.fuel_cost[n]
                    add_to_expression!(cost, ps_vars.gen_output[g, n, t],
                                      fuel_cost + fixed_cost + maint_cost)
                end
            end
        end

        if tech_output !== nothing
            n_tech = length(input.technologies)
            for tc in 1:n_tech
                tech = input.technologies[tc]
                for b in 1:n_buses
                    total_cost = tech.fuel_cost[b] + tech.fixed_cost[b] + tech.maintenance_cost[b]
                    if total_cost > 0
                        add_to_expression!(cost, tech_output[tc, b, t], total_cost)
                    end
                end
            end
        end

        # load_shed is per-bus (B2 refactor) — iterate over buses.
        for b in 1:n_buses
            add_to_expression!(cost, ps_vars.load_shed[b, t], input.loss_of_load_penalty)
        end

        for n in 1:n_nodes
            add_to_expression!(cost, ps_vars.fre_penetration_loss[n, t],
                input.fre_penetration_loss_penalty)
        end

        # Curtailment penalty (existing renewable generators).
        # Tech-investment curtailment is penalised separately in
        # add_day_operational_constraints! where the tech_output expression
        # and per-year cumul_cap are accessible. Without this term the master
        # has no incentive to size tech to actual absorption capacity —
        # excess renewable is spilled "for free", so master overbuilds and
        # operational reveals the gap (curtailment 40-65% from year 2042 on
        # despite the configured max_curtailment_ratio = 5%).
        for n in 1:n_nodes
            add_to_expression!(cost, ps_vars.curtailment[n, t],
                input.curtailment_cost)
        end

        if ps_vars.loss_of_load_sectoral !== nothing && !isempty(ps_vars.loss_of_load_sectoral)
            for (sector, lol_vars) in ps_vars.loss_of_load_sectoral
                criticality = get(input.sectoral_criticality, sector, 1.0)
                sector_penalty = input.loss_of_load_penalty * criticality
                for n in 1:n_buses
                    add_to_expression!(cost, lol_vars[n, t], sector_penalty)
                end
            end
        end
    end

    n_bat = length(input.batteries)
    for t in 1:hours
        for b in 1:n_bat
            bat = input.batteries[b]
            for n in ps_vars.buses_of_bat[b]
                if haskey(ps_vars.bat_seg_discharge, (b, n))
                    segs = input.bat_cost_curves[b][n]
                    seg_vars = ps_vars.bat_seg_discharge[(b, n)]
                    for k in 1:length(segs)
                        add_to_expression!(cost, segs[k].marginal_cost, seg_vars[k, t])
                    end
                else
                    add_to_expression!(cost, ps_vars.bat_discharge[b, n, t],
                                      bat.throughput_degradation_cost[n])
                end
            end
        end
    end

    for t in 1:hours
        for n in 1:n_nodes
            add_to_expression!(cost, ps_vars.reserve_static_loss[n, t],
                input.loss_of_reserve_static)
            add_to_expression!(cost, ps_vars.reserve_dynamic_loss[n, t],
                input.loss_of_reserve_dynamic)
        end
    end

    if ps_vars.loss_of_inertia !== nothing
        for t in 1:hours
            add_to_expression!(cost, ps_vars.loss_of_inertia[t],
                input.loss_of_inertia_penalty)
        end
    end

    tres = max(1.0, input.temporal_resolution_hours)
    days_in_year = length(input.hours_per_year) > 0 ? input.hours_per_year[1] / 24.0 : 365.0
    scaling_factor = tres * days_in_year / input.representative_days_per_year

    return cost * scaling_factor
end

"""
    add_representative_days_validation!(model, vars, input, targets)

Add operational validation through representative days.
This is CRITICAL for linking investment decisions to actual generation feasibility.

Exactly mirrors legacy _add_representative_days_validation.
"""
function add_representative_days_validation!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput,
    targets::Dict{Tuple{Int,Int}, Float64}
)
    num_years = length(input.years)
    timesteps_per_day = 24 ÷ input.temporal_resolution_hours

    for y_idx in 1:num_years
        timesteps_per_year = input.hours_per_year[y_idx] ÷ input.temporal_resolution_hours

        rep_days = select_representative_days(
            input.base_demand,
            y_idx,
            input.representative_days_per_year,
            input.min_day_separation,
            timesteps_per_day,
            timesteps_per_year,
        )

        if isempty(rep_days)
            @warn "No representative days selected for year $(input.years[y_idx])"
            continue
        end

        vars.operational_costs[y_idx] = AffExpr[]

        for (day_idx, start_hour) in enumerate(rep_days)
            end_hour = min(start_hour + timesteps_per_day - 1, size(input.base_demand, 1))
            hours = end_hour - start_hour + 1

            if hours < 1
                continue
            end

            day_demand = input.base_demand[start_hour:end_hour, :]

            # Create PowerSystemVariables for this day
            ps_vars = create_day_ps_vars!(model, input, y_idx, day_idx, hours)

            # Add operational constraints via delegation to power_system.jl
            tech_output, tech_curt_cost = add_day_operational_constraints!(
                model, ps_vars, vars, input, y_idx, day_idx, day_demand, start_hour
            )

            # Calculate and store operational cost (including tech curtailment penalty)
            day_cost = calculate_day_operational_cost(ps_vars, input, y_idx, hours;
                tech_output=tech_output) + tech_curt_cost
            push!(vars.operational_costs[y_idx], day_cost)
        end
    end
end

# =============================================================================
# TSAM (Time-Series Aggregation Method)
# =============================================================================

"""
    calculate_day_operational_cost_tsam(day_vars, input, year_idx, hours, weight)

Calculate operational cost for a TSAM representative period with period weight.
Uses weight-based scaling instead of 365/N uniform scaling.
"""
function calculate_day_operational_cost_tsam(
    ps_vars::PowerSystemVariables,
    input::MasterProblemInput,
    year_idx::Int,
    hours::Int,
    weight::Float64;
    tech_output::Union{Nothing, Any} = nothing
)::AffExpr
    n_gen = length(input.generators)
    n_buses = input.network.num_buses
    n_nodes = input.network.num_nodes

    cost = AffExpr(0.0)

    for t in 1:hours
        for g in 1:n_gen
            gen = input.generators[g]
            for n in ps_vars.buses_of_gen[g]
                fixed_cost = gen.fixed_cost[n]
                maint_cost = gen.maintenance_cost[n]
                if haskey(ps_vars.gen_seg_output, (g, n))
                    segs = input.gen_cost_curves[g][n]
                    seg_vars = ps_vars.gen_seg_output[(g, n)]
                    for k in 1:length(segs)
                        add_to_expression!(cost, segs[k].marginal_cost, seg_vars[k, t])
                    end
                    add_to_expression!(cost, ps_vars.gen_output[g, n, t],
                                      fixed_cost + maint_cost)
                else
                    fuel_cost = gen.fuel_cost[n]
                    add_to_expression!(cost, ps_vars.gen_output[g, n, t],
                                      fuel_cost + fixed_cost + maint_cost)
                end
            end
        end

        if tech_output !== nothing
            n_tech = length(input.technologies)
            for tc in 1:n_tech
                tech = input.technologies[tc]
                for b in 1:n_buses
                    total_cost = tech.fuel_cost[b] + tech.fixed_cost[b] + tech.maintenance_cost[b]
                    if total_cost > 0
                        add_to_expression!(cost, tech_output[tc, b, t], total_cost)
                    end
                end
            end
        end

        # load_shed is per-bus (B2 refactor) — iterate over buses.
        for b in 1:n_buses
            add_to_expression!(cost, ps_vars.load_shed[b, t], input.loss_of_load_penalty)
        end

        for n in 1:n_nodes
            add_to_expression!(cost, ps_vars.fre_penetration_loss[n, t],
                input.fre_penetration_loss_penalty)
        end

        # Curtailment penalty (existing renewable generators).
        # Tech-investment curtailment is penalised separately in
        # add_day_operational_constraints! where the tech_output expression
        # and per-year cumul_cap are accessible. Without this term the master
        # has no incentive to size tech to actual absorption capacity —
        # excess renewable is spilled "for free", so master overbuilds and
        # operational reveals the gap (curtailment 40-65% from year 2042 on
        # despite the configured max_curtailment_ratio = 5%).
        for n in 1:n_nodes
            add_to_expression!(cost, ps_vars.curtailment[n, t],
                input.curtailment_cost)
        end

        if ps_vars.loss_of_load_sectoral !== nothing && !isempty(ps_vars.loss_of_load_sectoral)
            for (sector, lol_vars) in ps_vars.loss_of_load_sectoral
                criticality = get(input.sectoral_criticality, sector, 1.0)
                sector_penalty = input.loss_of_load_penalty * criticality
                for n in 1:n_buses
                    add_to_expression!(cost, lol_vars[n, t], sector_penalty)
                end
            end
        end
    end

    n_bat = length(input.batteries)
    for t in 1:hours
        for b in 1:n_bat
            bat = input.batteries[b]
            for n in ps_vars.buses_of_bat[b]
                if haskey(ps_vars.bat_seg_discharge, (b, n))
                    segs = input.bat_cost_curves[b][n]
                    seg_vars = ps_vars.bat_seg_discharge[(b, n)]
                    for k in 1:length(segs)
                        add_to_expression!(cost, segs[k].marginal_cost, seg_vars[k, t])
                    end
                else
                    add_to_expression!(cost, ps_vars.bat_discharge[b, n, t],
                                      bat.throughput_degradation_cost[n])
                end
            end
        end
    end

    tres = max(1.0, input.temporal_resolution_hours)
    return cost * tres * weight
end

"""
    add_tsam_periods_validation!(model, vars, input, targets)

Add operational validation using TSAM representative periods with inter-period
SOC linking. Replaces add_representative_days_validation! when use_tsam=true.

Creates inter-period SOC boundary variables that form a chronological chain
across representative periods, enabling seasonal storage representation.
"""
function add_tsam_periods_validation!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput,
    targets::Dict{Tuple{Int,Int}, Float64}
)
    num_years = length(input.years)
    n_bat = length(input.batteries)
    n_buses = input.network.num_buses
    b2n = input.network.bus_to_node
    timesteps_per_day = 24 ÷ input.temporal_resolution_hours
    # Seasonal hydro: any reservoir unit makes the inter-period reservoir chain
    # relevant (gated on the same tsam_inter_period_linking flag as batteries).
    has_reservoir = any(any(g.reservoir_capacity .> 0) for g in input.generators)

    for y_idx in 1:num_years
        period_starts = input.tsam_period_start_hours[y_idx]
        period_weights = input.tsam_period_weights[y_idx]
        chrono_order = input.tsam_chronological_order[y_idx]
        K = length(period_starts)

        if K < 1
            @warn "No TSAM periods for year $(input.years[y_idx])"
            continue
        end

        vars.operational_costs[y_idx] = AffExpr[]

        # --- Create inter-period SOC boundary variables ---
        if input.tsam_inter_period_linking && n_bat > 0
            vars.inter_period_soc[y_idx] = Dict{Tuple{Int,Int,Int}, VariableRef}()

            for b in 1:n_bat
                bat = input.batteries[b]
                for n in 1:n_buses
                    # Compute total capacity (existing + invested) at this year
                    base_cap = bat.capacity[n]
                    lifetime = bat.life_time[n]
                    initial_age = bat.initial_age[n]
                    age_at_year = initial_age + (y_idx - 1)

                    total_cap = AffExpr(0.0)
                    if age_at_year < lifetime && base_cap > 0
                        add_to_expression!(total_cap, base_cap)
                    end

                    # Create K+1 boundary variables (0=year start, 1..K=after each period)
                    for p in 0:K
                        v = @variable(model,
                            lower_bound = 0,
                            base_name = "soc_bnd_y$(y_idx)_b$(b)_n$(n)_p$(p)"
                        )
                        @constraint(model, v <= total_cap,
                            base_name = "soc_bnd_cap_y$(y_idx)_b$(b)_n$(n)_p$(p)")
                        vars.inter_period_soc[y_idx][(b, n, p)] = v
                    end

                    # Year-cyclic: SOC at year end == SOC at year start
                    @constraint(model,
                        vars.inter_period_soc[y_idx][(b, n, K)] ==
                        vars.inter_period_soc[y_idx][(b, n, 0)],
                        base_name = "soc_year_cyclic_y$(y_idx)_b$(b)_n$(n)"
                    )
                end
            end
        end

        # --- Create inter-period reservoir-level boundary variables ---
        # One chronological chain of energy levels per reservoir gen-node, so
        # water can be moved across seasons. Bounded by the existing reservoir
        # capacity (investment expansion is not reflected at the boundary, the
        # same simplification used for batteries above).
        if input.tsam_inter_period_linking && has_reservoir
            vars.inter_period_reservoir[y_idx] = Dict{Tuple{Int,Int,Int}, VariableRef}()

            for g in 1:length(input.generators)
                gen = input.generators[g]
                for n in 1:n_buses
                    res_cap = gen.reservoir_capacity[n]
                    res_cap > 0 || continue
                    lo = gen.reservoir_min_level[n] * res_cap
                    hi = gen.reservoir_max_level[n] * res_cap

                    for p in 0:K
                        v = @variable(model,
                            lower_bound = lo, upper_bound = hi,
                            base_name = "reslvl_bnd_y$(y_idx)_g$(g)_n$(n)_p$(p)")
                        vars.inter_period_reservoir[y_idx][(g, n, p)] = v
                    end

                    # Year-cyclic: reservoir level at year end == year start
                    @constraint(model,
                        vars.inter_period_reservoir[y_idx][(g, n, K)] ==
                        vars.inter_period_reservoir[y_idx][(g, n, 0)],
                        base_name = "reslvl_year_cyclic_y$(y_idx)_g$(g)_n$(n)")
                end
            end
        end

        # --- Process each period in chronological order ---
        for (chrono_idx, period_idx) in enumerate(chrono_order)
            start_hour = period_starts[period_idx]
            weight = period_weights[period_idx]
            end_hour = min(start_hour + timesteps_per_day - 1, size(input.base_demand, 1))
            hours = end_hour - start_hour + 1
            if hours < 1
                continue
            end

            day_demand = input.base_demand[start_hour:end_hour, :]

            # Create PowerSystemVariables for this period
            ps_vars = create_day_ps_vars!(model, input, y_idx, chrono_idx, hours)

            # Build inter-period override dicts when seasonal linking is active.
            # Batteries chain SOC, reservoirs chain water level; both attach the
            # period start to the previous boundary and its end to the next.
            tech_output = nothing
            tech_curt_cost = AffExpr(0.0)

            soc_initial = nothing
            soc_final = nothing
            if input.tsam_inter_period_linking && n_bat > 0
                soc_initial = Dict{Tuple{Int,Int}, Any}()
                soc_final = Dict{Tuple{Int,Int}, Any}()
                for b in 1:n_bat
                    for n in 1:n_buses
                        soc_initial[(b, n)] = vars.inter_period_soc[y_idx][(b, n, chrono_idx - 1)]
                        soc_final[(b, n)] = vars.inter_period_soc[y_idx][(b, n, chrono_idx)]
                    end
                end
            end

            res_initial = nothing
            res_final = nothing
            if input.tsam_inter_period_linking && has_reservoir
                res_initial = Dict{Tuple{Int,Int}, Any}()
                res_final = Dict{Tuple{Int,Int}, Any}()
                for g in 1:length(input.generators)
                    gen = input.generators[g]
                    for n in 1:n_buses
                        gen.reservoir_capacity[n] > 0 || continue
                        res_initial[(g, n)] = vars.inter_period_reservoir[y_idx][(g, n, chrono_idx - 1)]
                        res_final[(g, n)] = vars.inter_period_reservoir[y_idx][(g, n, chrono_idx)]
                    end
                end
            end

            tech_output, tech_curt_cost = add_day_operational_constraints!(
                model, ps_vars, vars, input, y_idx, chrono_idx,
                day_demand, start_hour;
                initial_soc_overrides=soc_initial,
                final_soc_targets=soc_final,
                initial_reservoir_overrides=res_initial,
                final_reservoir_targets=res_final
            )

            # Calculate weighted operational cost (with tech curtailment penalty)
            day_cost = calculate_day_operational_cost_tsam(
                ps_vars, input, y_idx, hours, weight;
                tech_output=tech_output
            ) + weight * tech_curt_cost
            push!(vars.operational_costs[y_idx], day_cost)
        end
    end
end

# =============================================================================
# Main API
# =============================================================================

"""
    create_master_problem(input; use_representative_days=true)

Create the Master Problem optimization model.

# Arguments
- `input::MasterProblemInput`: Master problem configuration
- `use_representative_days::Bool`: Whether to add representative day subproblems
                                   CRITICAL: Must be true for correct RE target enforcement!

# Returns
- `model::Model`: JuMP model
- `vars::MasterProblemVariables`: Variable container
- `targets::Dict{Tuple{Int,Int}, Float64}`: RE targets by (system, year)
"""
function create_master_problem(
    input::MasterProblemInput;
    use_representative_days::Bool = true
)
    # Create model with optimizer
    model = Model(create_optimizer(
        solver_name=input.solver_name,
        threads=input.threads,
        time_limit=input.time_limit,
        gap=input.gap,
        verbose=input.verbose,
        solver_options=input.solver_options
    ))

    # Calculate RE targets
    targets = calculate_target_ratios(input)

    # Create variables
    vars = build_master_variables!(model, input)

    # Add constraints
    add_investment_constraints!(model, vars, input)
    add_budget_constraints!(model, vars, input)
    # Retirement cascade constraints - ensures once retired, stays retired
    add_retirement_cascade_constraints!(model, vars, input)
    add_capacity_adequacy_constraints!(model, vars, input)
    add_transmission_symmetry_constraints!(model, vars, input)

    # CRITICAL: Add representative days / TSAM periods validation
    # This links investment decisions to operational feasibility and RE targets
    if use_representative_days
        if input.use_tsam
            add_tsam_periods_validation!(model, vars, input, targets)
        else
            add_representative_days_validation!(model, vars, input, targets)
        end
    end

    # Also add the simple RE target constraint as a fallback
    # (but the real enforcement comes from representative days)
    add_re_target_constraints!(model, vars, input, targets)

    # Add RE increment constraints (min/max annual change in RE ratio)
    add_re_increment_constraints!(model, vars, input)

    # Build objective (now includes operational costs from representative days)
    build_master_objective!(model, vars, input)

    return model, vars, targets
end

"""
    extract_master_solution(model, vars, input)

Extract solution values from solved Master Problem.
"""
function extract_master_solution(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput
)::MasterProblemResult
    num_years = length(input.years)
    n_buses = input.network.num_buses
    b2n = input.network.bus_to_node
    n_gen = length(input.generators)
    n_bat = length(input.batteries)

    # Extract investment decisions
    n_tech = length(input.technologies)
    n_bat_tech = length(input.battery_technologies)
    tech_inv = Dict{Int, Dict{Int, Vector{Float64}}}()
    bat_tech_pow_inv = Dict{Int, Dict{Int, Vector{Float64}}}()
    bat_tech_cap_inv = Dict{Int, Dict{Int, Vector{Float64}}}()
    trans_inv = Dict{Int, Dict{Tuple{Int,Int}, Float64}}()

    gen_life = Dict{Int, Dict{Int, Vector{Float64}}}()
    bat_life = Dict{Int, Dict{Int, Vector{Float64}}}()

    total_inv_by_year = zeros(num_years)
    total_op_by_year = zeros(num_years)
    re_by_year = zeros(num_years)

    cumul_gen = Dict{Int, Dict{Int, Vector{Float64}}}()
    cumul_bat = Dict{Int, Dict{Int, Vector{Float64}}}()
    cumul_bat_power = Dict{Int, Dict{Int, Vector{Float64}}}()
    cumul_tech = Dict{Int, Dict{Int, Vector{Float64}}}()
    cumul_bat_tech_pow = Dict{Int, Dict{Int, Vector{Float64}}}()
    cumul_bat_tech_cap = Dict{Int, Dict{Int, Vector{Float64}}}()

    for y_idx in 1:num_years
        tech_inv[y_idx] = Dict{Int, Vector{Float64}}()
        bat_tech_pow_inv[y_idx] = Dict{Int, Vector{Float64}}()
        bat_tech_cap_inv[y_idx] = Dict{Int, Vector{Float64}}()
        trans_inv[y_idx] = Dict{Tuple{Int,Int}, Float64}()
        gen_life[y_idx] = Dict{Int, Vector{Float64}}()
        bat_life[y_idx] = Dict{Int, Vector{Float64}}()
        cumul_gen[y_idx] = Dict{Int, Vector{Float64}}()
        cumul_bat[y_idx] = Dict{Int, Vector{Float64}}()
        cumul_bat_power[y_idx] = Dict{Int, Vector{Float64}}()
        cumul_tech[y_idx] = Dict{Int, Vector{Float64}}()
        cumul_bat_tech_pow[y_idx] = Dict{Int, Vector{Float64}}()
        cumul_bat_tech_cap[y_idx] = Dict{Int, Vector{Float64}}()

        # Existing generator cumulative capacity (no investments, existing + life extension only)
        for g in 1:n_gen
            gen = input.generators[g]
            cumul_gen[y_idx][g] = zeros(n_buses)
            for n in 1:n_buses
                age_at_year = gen.initial_age[n] + (y_idx - 1)
                deg_rate = gen.degradation_rate[n]
                if age_at_year < gen.life_time[n] && gen.rated_power[n] > 0
                    cumul_gen[y_idx][g][n] = gen.rated_power[n] * (1.0 - deg_rate) ^ age_at_year
                elseif gen.rated_power[n] > 0
                    life_ext_var = vars.gen_life_extension[y_idx][g][n]
                    if life_ext_var !== nothing
                        cumul_gen[y_idx][g][n] += value(life_ext_var)
                    end
                end
            end
            gen_life[y_idx][g] = zeros(n_buses)
            for n in 1:n_buses
                age_at_year = gen.initial_age[n] + (y_idx - 1)
                gen_life[y_idx][g][n] = age_at_year < gen.life_time[n] ? 1.0 : 0.0
            end
        end

        # Existing battery cumulative capacity (no investments, existing + life extension only)
        for b in 1:n_bat
            bat = input.batteries[b]
            cumul_bat[y_idx][b] = zeros(n_buses)
            cumul_bat_power[y_idx][b] = zeros(n_buses)
            for n in 1:n_buses
                age_at_year = bat.initial_age[n] + (y_idx - 1)
                if age_at_year < bat.life_time[n]
                    if bat.capacity[n] > 0
                        cumul_bat[y_idx][b][n] = bat.capacity[n]
                    end
                    if bat.max_discharge_power[n] > 0
                        cumul_bat_power[y_idx][b][n] = bat.max_discharge_power[n]
                    end
                elseif bat.max_discharge_power[n] > 0
                    life_ext_var = vars.bat_life_extension[y_idx][b][n]
                    if life_ext_var !== nothing
                        life_ext_val = value(life_ext_var)
                        cumul_bat_power[y_idx][b][n] += life_ext_val
                        if bat.max_discharge_power[n] > 0
                            ratio = bat.capacity[n] / bat.max_discharge_power[n]
                            cumul_bat[y_idx][b][n] += life_ext_val * ratio
                        end
                    end
                end
            end
            bat_life[y_idx][b] = zeros(n_buses)
            for n in 1:n_buses
                age_at_year = bat.initial_age[n] + (y_idx - 1)
                bat_life[y_idx][b][n] = age_at_year < bat.life_time[n] ? 1.0 : 0.0
            end
        end

        # Technology investment extraction
        yp = inv_period_year(y_idx, vars.years_per_inv_period)
        for t in 1:n_tech
            tech = input.technologies[t]
            # Investment value: only non-zero at period start, zero otherwise
            if y_idx == yp
                tech_inv[y_idx][t] = [value(vars.tech_investment[yp][t][n]) for n in 1:n_buses]
            else
                tech_inv[y_idx][t] = zeros(n_buses)
            end
            # Cumulative with degradation and retirement (sum over period starts only)
            cumul_tech[y_idx][t] = zeros(n_buses)
            for n in 1:n_buses
                for y in 1:vars.years_per_inv_period:y_idx
                    inv_age = y_idx - y
                    if inv_age < tech.life_time[n]
                        deg = (1.0 - tech.degradation_rate[n]) ^ inv_age
                        cumul_tech[y_idx][t][n] += value(vars.tech_investment[y][t][n]) * deg
                    end
                end
            end
            for n in 1:n_buses
                total_inv_by_year[y_idx] += tech_inv[y_idx][t][n] * tech.invest_cost[n]
            end
        end

        # Battery technology investment extraction
        for bt in 1:n_bat_tech
            btech = input.battery_technologies[bt]
            if y_idx == yp
                bat_tech_pow_inv[y_idx][bt] = [value(vars.bat_tech_power_investment[yp][bt][n]) for n in 1:n_buses]
                bat_tech_cap_inv[y_idx][bt] = [value(vars.bat_tech_capacity_investment[yp][bt][n]) for n in 1:n_buses]
            else
                bat_tech_pow_inv[y_idx][bt] = zeros(n_buses)
                bat_tech_cap_inv[y_idx][bt] = zeros(n_buses)
            end
            cumul_bat_tech_pow[y_idx][bt] = zeros(n_buses)
            cumul_bat_tech_cap[y_idx][bt] = zeros(n_buses)
            for n in 1:n_buses
                for y in 1:vars.years_per_inv_period:y_idx
                    inv_age = y_idx - y
                    if inv_age < btech.life_time[n]
                        cumul_bat_tech_pow[y_idx][bt][n] += value(vars.bat_tech_power_investment[y][bt][n])
                        cumul_bat_tech_cap[y_idx][bt][n] += value(vars.bat_tech_capacity_investment[y][bt][n])
                    end
                end
            end
            for n in 1:n_buses
                total_inv_by_year[y_idx] += bat_tech_pow_inv[y_idx][bt][n] * btech.invest_cost_power[n]
                total_inv_by_year[y_idx] += bat_tech_cap_inv[y_idx][bt][n] * btech.invest_cost_capacity[n]
            end
        end

        # Transmission investment
        if haskey(vars.transfer_investment, yp)
            for ((i, j), var) in vars.transfer_investment[yp]
                if y_idx == yp
                    trans_inv[y_idx][(i, j)] = value(var)
                else
                    trans_inv[y_idx][(i, j)] = 0.0
                end
            end
        end

        # RE penetration — average across systems weighted by demand
        n_sys = max(1, length(input.system_node_ranges))
        re_sum = 0.0
        for s_idx in 1:n_sys
            re_sum += value(vars.re_penetration_ratio[(s_idx, y_idx)])
        end
        re_by_year[y_idx] = re_sum / n_sys  # unweighted mean across systems
    end

    # Per-system RE penetration
    re_by_system = Dict{String, Vector{Float64}}()
    n_sys = max(1, length(input.system_node_ranges))
    for s_idx in 1:n_sys
        sname = isempty(input.system_node_ranges) ? "global" : input.system_node_ranges[s_idx].name
        re_by_system[sname] = [value(vars.re_penetration_ratio[(s_idx, y_idx)]) for y_idx in 1:num_years]
    end

    # Extract reservoir investment
    res_inv = Dict{Int, Dict{Int, Vector{Float64}}}()
    for y_idx in 1:num_years
        res_inv[y_idx] = Dict{Int, Vector{Float64}}()
        for g in 1:n_gen
            if haskey(vars.reservoir_investment[y_idx], g)
                res_inv[y_idx][g] = [value(vars.reservoir_investment[y_idx][g][n]) for n in 1:n_buses]
                # Add to investment cost summary
                gen = input.generators[g]
                for n in 1:n_buses
                    total_inv_by_year[y_idx] += res_inv[y_idx][g][n] * gen.reservoir_invest_cost[n]
                end
            end
        end
    end

    return MasterProblemResult(
        termination_status(model),
        objective_value(model),
        solve_time(model),
        tech_inv,
        bat_tech_pow_inv,
        bat_tech_cap_inv,
        trans_inv,
        gen_life,
        bat_life,
        total_inv_by_year,
        total_op_by_year,
        re_by_year,
        re_by_system,
        cumul_gen,
        cumul_bat,
        cumul_bat_power,
        cumul_tech,
        cumul_bat_tech_pow,
        cumul_bat_tech_cap,
        res_inv
    )
end

# =============================================================================
# Multi-System Support
# =============================================================================

"""
    _build_sys_input(input::MultiSystemMasterInput, sys::SystemConfig)

Build a single-system `MasterProblemInput` from multi-system input and system config.
Extracts per-system fields from `sys` and shared fields from `input`.
"""
function _build_sys_input(input::MultiSystemMasterInput, sys::SystemConfig)
    return MasterProblemInput(
        years = input.years,
        base_year = input.base_year,
        system_name = sys.name,
        network = sys.network,
        generators = sys.generators,
        batteries = sys.batteries,
        base_demand = sys.base_demand,
        demand_growth = input.demand_growth,
        discount_rate = input.discount_rate,
        max_annual_investment = input.max_annual_investment,
        target_re_penetration = sys.target_re_penetration,
        initial_re_penetration = sys.initial_re_penetration,
        slack_penalty = input.slack_penalty,
        loss_of_load_penalty = input.loss_of_load_penalty,
        fre_penetration_loss_penalty = input.fre_penetration_loss_penalty,
        max_curtailment_ratio = input.max_curtailment_ratio,
        temporal_resolution_hours = input.temporal_resolution_hours,
        representative_days_per_year = input.representative_days_per_year,
        min_day_separation = input.min_day_separation,
        life_extension_cost_factor = input.life_extension_cost_factor,
        decommissioning_cost_factor = input.decommissioning_cost_factor,
        hours_per_year = input.hours_per_year,
        threads = input.threads,
        time_limit = input.time_limit,
        gap = input.gap,
        verbose = input.verbose
    )
end

"""
    create_multi_system_master_problem(input::MultiSystemMasterInput)

Create a Master Problem for multiple interconnected systems.

Returns model, extended variables, and per-system RE targets.
"""
function create_multi_system_master_problem(input::MultiSystemMasterInput)
    model = Model(create_optimizer(
        solver_name=input.solver_name,
        threads=input.threads,
        time_limit=input.time_limit,
        gap=input.gap,
        verbose=input.verbose,
        solver_options=input.solver_options
    ))

    num_years = length(input.years)
    num_links = length(input.inter_system_links)

    # Create per-system variables and cache MasterProblemInput
    system_vars = Dict{String, MasterProblemVariables}()
    system_targets = Dict{String, Dict{Tuple{Int,Int}, Float64}}()
    sys_inputs = Dict{String, MasterProblemInput}()

    for sys in input.systems
        sys_input = _build_sys_input(input, sys)
        sys_inputs[sys.name] = sys_input

        # Build variables for this system
        vars = build_master_variables!(model, sys_input)
        system_vars[sys.name] = vars

        # Calculate RE targets for this system
        system_targets[sys.name] = calculate_target_ratios(sys_input)
    end

    # Create inter-system investment variables
    inter_system_investment = Dict{Int, Dict{Int, VariableRef}}()
    for y_idx in 1:num_years
        inter_system_investment[y_idx] = Dict{Int, VariableRef}()
        for (link_idx, link) in enumerate(input.inter_system_links)
            inter_system_investment[y_idx][link_idx] = @variable(model,
                lower_bound = 0,
                upper_bound = link.max_investment_mw,
                base_name = "inter_inv_y$(y_idx)_l$(link_idx)"
            )
        end
    end

    # Create extended variables container
    ext_vars = ExtendedMasterVariables(
        system_vars,
        inter_system_investment,
        Dict{String, Dict{Int, Vector{VariableRef}}}(),
        Dict{String, Dict{Int, Dict{Int, VariableRef}}}(),
        Dict{String, Dict{Int, Vector{AffExpr}}}(),
        Dict{String, Dict{Int, AffExpr}}(),
        Dict{String, Dict{Int, Float64}}(),
        Dict{Tuple{Int,Int,Int,Int}, VariableRef}(),
        Dict{Tuple{Int,Int,Int,Int}, VariableRef}(),
        Dict{Tuple{Int,Int,Int,Int}, AffExpr}(),
        Dict{String, Dict{Tuple{Int,Int}, Any}}()
    )

    # Add per-system constraints
    for sys in input.systems
        sys_input = sys_inputs[sys.name]
        vars = system_vars[sys.name]
        targets = system_targets[sys.name]

        add_investment_constraints!(model, vars, sys_input)
        add_budget_constraints!(model, vars, sys_input)
        add_retirement_cascade_constraints!(model, vars, sys_input)
        add_re_target_constraints!(model, vars, sys_input, targets)
        add_capacity_adequacy_constraints!(model, vars, sys_input)
        add_transmission_symmetry_constraints!(model, vars, sys_input)
    end

    # Add inter-system constraints (investment-level)
    add_inter_system_constraints!(model, ext_vars, input)

    # =======================================================================
    # Representative days with DC-OPF inter-system coupling
    # Key: create inter-system flow variables BEFORE per-system KCL
    # =======================================================================
    timesteps_per_day = 24 ÷ input.temporal_resolution_hours
    n_seg = input.inter_system_loss_segments

    # Initialize day_vars storage per system
    for sys in input.systems
        ext_vars.system_day_vars[sys.name] = Dict{Tuple{Int,Int}, Any}()
    end

    for y_idx in 1:num_years
        timesteps_per_year = input.hours_per_year[y_idx] ÷ input.temporal_resolution_hours

        rep_days = select_representative_days(
            input.systems[1].base_demand, y_idx,
            input.representative_days_per_year,
            input.min_day_separation,
            timesteps_per_day, timesteps_per_year
        )
        if isempty(rep_days)
            continue
        end

        # Initialize operational costs for this year (per system)
        for sys in input.systems
            system_vars[sys.name].operational_costs[y_idx] = AffExpr[]
        end

        for (day_idx, start_hour) in enumerate(rep_days)
            end_hour = min(start_hour + timesteps_per_day - 1,
                           size(input.systems[1].base_demand, 1))
            hours = end_hour - start_hour + 1
            if hours < 1 continue end

            # === STEP A: Create inter-system DC-OPF flow variables ===
            external_inj_per_system = Dict{String, Dict{Tuple{Int,Int}, AffExpr}}()
            for sys in input.systems
                external_inj_per_system[sys.name] = Dict{Tuple{Int,Int}, AffExpr}()
            end

            for (link_idx, link) in enumerate(input.inter_system_links)
                # Cumulative capacity: existing + invested up to this year
                total_cap = AffExpr(link.existing_capacity_mw)
                for y in 1:y_idx
                    if haskey(ext_vars.inter_system_investment, y) &&
                       haskey(ext_vars.inter_system_investment[y], link_idx)
                        add_to_expression!(total_cap,
                            ext_vars.inter_system_investment[y][link_idx])
                    end
                end

                # PWL precomputation
                R_l = link.resistance_pu
                X_l = link.reactance_pu
                f_max = link.existing_capacity_mw + link.max_investment_mw
                g_l = (R_l > 0 && X_l > 0) ? R_l / (R_l^2 + X_l^2) : 0.0
                use_pwl = n_seg > 0 && g_l > 0 && f_max > 0

                for t in 1:hours
                    # Bidirectional flow variable (positive = from→to)
                    pf = @variable(model,
                        base_name="inter_pf_y$(y_idx)_d$(day_idx)_l$(link_idx)_t$(t)")
                    @constraint(model, pf <= total_cap,
                        base_name="inter_cap_pos_y$(y_idx)_d$(day_idx)_l$(link_idx)_t$(t)")
                    @constraint(model, pf >= -total_cap,
                        base_name="inter_cap_neg_y$(y_idx)_d$(day_idx)_l$(link_idx)_t$(t)")
                    ext_vars.inter_system_pf[(y_idx, day_idx, link_idx, t)] = pf

                    # Direction decomposition: pf = fp - fn
                    fp = @variable(model, lower_bound=0,
                        base_name="inter_fp_y$(y_idx)_d$(day_idx)_l$(link_idx)_t$(t)")
                    fn = @variable(model, lower_bound=0,
                        base_name="inter_fn_y$(y_idx)_d$(day_idx)_l$(link_idx)_t$(t)")
                    @constraint(model, pf == fp - fn,
                        base_name="inter_decomp_y$(y_idx)_d$(day_idx)_l$(link_idx)_t$(t)")

                    # |flow| = fp + fn for objective cost
                    abs_flow = AffExpr(0.0)
                    add_to_expression!(abs_flow, fp)
                    add_to_expression!(abs_flow, fn)
                    ext_vars.inter_system_abs_flow[(y_idx, day_idx, link_idx, t)] = abs_flow

                    # PWL loss computation
                    ploss = @variable(model, lower_bound=0,
                        base_name="inter_ploss_y$(y_idx)_d$(day_idx)_l$(link_idx)_t$(t)")

                    if use_pwl
                        Δf = f_max / n_seg
                        # Use base_impedance from the from-system for inter-system links
                        inter_s_base = sys_inputs[link.from_system].network.base_impedance
                        loss_expr = AffExpr(0.0)
                        fp_sum = AffExpr(0.0)
                        fn_sum = AffExpr(0.0)
                        for k in 1:n_seg
                            m_k = g_l * (2k - 1) * Δf / inter_s_base
                            dp = @variable(model, lower_bound=0, upper_bound=Δf,
                                base_name="inter_dp_l$(link_idx)_k$(k)_y$(y_idx)_d$(day_idx)_t$(t)")
                            dn = @variable(model, lower_bound=0, upper_bound=Δf,
                                base_name="inter_dn_l$(link_idx)_k$(k)_y$(y_idx)_d$(day_idx)_t$(t)")
                            add_to_expression!(fp_sum, dp)
                            add_to_expression!(fn_sum, dn)
                            add_to_expression!(loss_expr, m_k, dp)
                            add_to_expression!(loss_expr, m_k, dn)
                        end
                        @constraint(model, fp == fp_sum)
                        @constraint(model, fn == fn_sum)
                        @constraint(model, ploss == loss_expr)
                    else
                        # Linear loss fallback: ploss = loss_factor × |flow|
                        @constraint(model, ploss == link.loss_factor * abs_flow,
                            base_name="inter_linloss_y$(y_idx)_d$(day_idx)_l$(link_idx)_t$(t)")
                    end
                    ext_vars.inter_system_loss[(y_idx, day_idx, link_idx, t)] = ploss

                    # Build external injections for both systems (outflow convention)
                    # FROM bus: +pf (outgoing) + 0.5×ploss (bus supplies flow + half loss)
                    from_key = (link.from_node, t)
                    if !haskey(external_inj_per_system[link.from_system], from_key)
                        external_inj_per_system[link.from_system][from_key] = AffExpr(0.0)
                    end
                    add_to_expression!(
                        external_inj_per_system[link.from_system][from_key], 1.0, pf)
                    add_to_expression!(
                        external_inj_per_system[link.from_system][from_key], 0.5, ploss)

                    # TO bus: -pf (incoming) + 0.5×ploss (bus absorbs half loss)
                    to_key = (link.to_node, t)
                    if !haskey(external_inj_per_system[link.to_system], to_key)
                        external_inj_per_system[link.to_system][to_key] = AffExpr(0.0)
                    end
                    add_to_expression!(
                        external_inj_per_system[link.to_system][to_key], -1.0, pf)
                    add_to_expression!(
                        external_inj_per_system[link.to_system][to_key], 0.5, ploss)
                end
            end

            # === STEP B: Per-system operational constraints with external injections ===
            for sys in input.systems
                sys_input = sys_inputs[sys.name]
                vars_sys = system_vars[sys.name]

                day_demand = sys.base_demand[start_hour:end_hour, :]
                actual_hours = size(day_demand, 1)
                if actual_hours < 1 continue end

                ps_vars = create_day_ps_vars!(
                    model, sys_input, y_idx, day_idx, actual_hours)

                # Pass inter-system flow contributions at border buses
                inj = external_inj_per_system[sys.name]
                tech_output, tech_curt_cost = add_day_operational_constraints!(
                    model, ps_vars, vars_sys, sys_input,
                    y_idx, day_idx, day_demand, start_hour;
                    external_injections = isempty(inj) ? nothing : inj
                )

                day_cost = calculate_day_operational_cost(
                    ps_vars, sys_input, y_idx, actual_hours;
                    tech_output=tech_output) + tech_curt_cost
                push!(vars_sys.operational_costs[y_idx], day_cost)
                ext_vars.system_day_vars[sys.name][(y_idx, day_idx)] = ps_vars
            end
        end
    end

    # Build multi-system objective
    build_multi_system_objective!(model, ext_vars, input)

    return model, ext_vars, system_targets
end

"""
    add_inter_system_constraints!(model, ext_vars, input)

Add constraints for inter-system transmission.
"""
function add_inter_system_constraints!(
    model::Model,
    ext_vars::ExtendedMasterVariables,
    input::MultiSystemMasterInput
)
    num_years = length(input.years)
    num_links = length(input.inter_system_links)

    # Cumulative inter-system investment limits
    for (link_idx, link) in enumerate(input.inter_system_links)
        if link.max_investment_mw > 0
            cumulative = sum(ext_vars.inter_system_investment[y][link_idx] for y in 1:num_years)
            @constraint(model,
                cumulative <= link.max_investment_mw,
                base_name = "cumul_inter_link$(link_idx)"
            )
        end
    end

    # Symmetry constraint for bidirectional links
    # Find pairs of links that are reverses of each other
    for (i, link_i) in enumerate(input.inter_system_links)
        for (j, link_j) in enumerate(input.inter_system_links)
            if j > i &&
               link_i.from_system == link_j.to_system &&
               link_i.to_system == link_j.from_system &&
               link_i.from_node == link_j.to_node &&
               link_i.to_node == link_j.from_node
                # Symmetric pair found
                for y_idx in 1:num_years
                    @constraint(model,
                        ext_vars.inter_system_investment[y_idx][i] ==
                        ext_vars.inter_system_investment[y_idx][j],
                        base_name = "inter_sym_$(i)_$(j)_y$(y_idx)"
                    )
                end
            end
        end
    end
end

"""
    build_multi_system_objective!(model, ext_vars, input)

Build objective function for multi-system Master Problem.
"""
function build_multi_system_objective!(
    model::Model,
    ext_vars::ExtendedMasterVariables,
    input::MultiSystemMasterInput
)
    num_years = length(input.years)
    total_cost = AffExpr(0.0)

    # Per-system investment and operational costs
    for sys in input.systems
        vars = ext_vars.system_vars[sys.name]
        n_buses = sys.network.num_buses
        b2n = sys.network.bus_to_node
        n_gen = length(sys.generators)
        n_bat = length(sys.batteries)
        ypp = vars.years_per_inv_period

        # Investment costs — only at period starts
        for y_idx in 1:ypp:num_years
            discount_factor = 1.0 / ((1.0 + input.discount_rate)^(y_idx - 1))
            investment_cost = AffExpr(0.0)

            # Technology investment
            n_tech = length(sys.technologies)
            for t in 1:n_tech
                tech = sys.technologies[t]
                for n in 1:n_buses
                    add_to_expression!(investment_cost,
                        vars.tech_investment[y_idx][t][n],
                        tech.invest_cost[n])
                end
            end

            # Battery technology investment
            n_bat_tech = length(sys.battery_technologies)
            for bt in 1:n_bat_tech
                bat_tech = sys.battery_technologies[bt]
                for n in 1:n_buses
                    add_to_expression!(investment_cost,
                        vars.bat_tech_power_investment[y_idx][bt][n],
                        bat_tech.invest_cost_power[n])
                    add_to_expression!(investment_cost,
                        vars.bat_tech_capacity_investment[y_idx][bt][n],
                        bat_tech.invest_cost_capacity[n])
                end
            end

            # Transmission investment
            for ((i, j), var) in vars.transfer_investment[y_idx]
                trans_cost = sys.network.transference_invest_cost[i]
                add_to_expression!(investment_cost, var, trans_cost)
            end

            # (Life extension / decommissioning costs removed - unified retirement)

            add_to_expression!(total_cost, investment_cost, discount_factor)
        end

        # Slack penalties — per year (not just period starts)
        for y_idx in 1:num_years
            add_to_expression!(total_cost, vars.slack_re_target[y_idx], input.slack_penalty)
            add_to_expression!(total_cost, vars.slack_budget[y_idx], input.slack_penalty)
            for ni in 1:input.network.num_nodes
                add_to_expression!(total_cost, vars.slack_capacity[(y_idx, ni)], input.slack_penalty)
            end
        end
    end

    # Inter-system investment costs
    for y_idx in 1:num_years
        discount_factor = 1.0 / ((1.0 + input.discount_rate)^(y_idx - 1))
        for (link_idx, link) in enumerate(input.inter_system_links)
            add_to_expression!(total_cost,
                ext_vars.inter_system_investment[y_idx][link_idx],
                link.investment_cost_per_mw * discount_factor)
        end

        # Per-system operational costs from representative days
        for sys in input.systems
            vars = ext_vars.system_vars[sys.name]
            if haskey(vars.operational_costs, y_idx)
                for day_cost in vars.operational_costs[y_idx]
                    add_to_expression!(total_cost, day_cost, discount_factor)
                end
            end
        end

        # Inter-system operational flow costs (distance × cost_per_mw_km × |flow|)
        for (link_idx, link) in enumerate(input.inter_system_links)
            distance_km = link.distance_km
            cost_per_mw_km = link.cost_per_mw_km
            if distance_km > 0 && cost_per_mw_km > 0
                flow_cost = distance_km * cost_per_mw_km
                for ((yi, di, li, t), abs_flow) in ext_vars.inter_system_abs_flow
                    if yi == y_idx && li == link_idx
                        add_to_expression!(total_cost, abs_flow, flow_cost * discount_factor)
                    end
                end
            end
        end
    end

    @objective(model, Min, total_cost)

    return total_cost  # Return for MGA near-optimal constraint
end

# =============================================================================
# Stochastic Programming Support
# =============================================================================

"""
    create_stochastic_master_problem(input::StochasticMasterInput)

Create a stochastic Master Problem with multiple scenarios.

Investment decisions are first-stage (common across scenarios).
Operational costs are second-stage (scenario-specific).
"""
function create_stochastic_master_problem(input::StochasticMasterInput)
    base_input = input.base_input

    # Create base model WITH representative days / TSAM — this populates
    # vars.operational_costs which the stochastic objective needs.
    model, vars, targets = create_master_problem(base_input; use_representative_days=true)

    if !input.use_stochastic || isempty(input.scenarios)
        return model, vars, targets, input.scenarios
    end

    # Replace the deterministic objective with scenario-weighted costs
    build_stochastic_objective!(model, vars, base_input, input.scenarios)

    return model, vars, targets, input.scenarios
end

"""
    apply_scenario_multipliers(input::MasterProblemInput, scenario::Scenario; year_idx=1)

Apply scenario multipliers to create scenario-specific input.
Returns modified copies of generators and batteries with scenario-adjusted costs.

`year_idx` controls fuel_price_growth compounding: fuel_cost × fuel_price_growth^(year_idx-1).
"""
function apply_scenario_multipliers(
    input::MasterProblemInput,
    scenario::Scenario;
    year_idx::Int = 1
)::Tuple{Vector{GeneratorConfig}, Vector{BatteryConfig}}
    mult = scenario.multipliers

    # Fuel price escalation: base multiplier × compounded growth
    fuel_price_mult = mult.fuel_cost * (mult.fuel_price_growth ^ (year_idx - 1))

    # Copy and modify generators
    modified_gens = Vector{GeneratorConfig}()
    for gen in input.generators
        is_renewable = gen.type == "Renewable"
        cost_mult = is_renewable ? mult.invest_cost_renewables : mult.invest_cost_conventional

        # Create new generator with modified costs
        new_invest_cost = gen.invest_cost .* cost_mult
        new_fuel_cost = gen.fuel_cost .* fuel_price_mult
        new_maintenance_cost = gen.maintenance_cost .* mult.maintenance_cost

        push!(modified_gens, GeneratorConfig(
            gen.name, gen.type, gen.fuel,
            gen.rated_power, gen.min_power,
            gen.efficiency_rated, gen.efficiency_min,
            gen.ramp_up, gen.ramp_down,
            gen.min_up_time, gen.min_down_time,
            gen.start_up_cost, new_fuel_cost,
            gen.fixed_cost, new_maintenance_cost,
            gen.inertia, new_invest_cost, gen.invest_max,
            gen.availability, gen.reservable,
            gen.life_time, gen.initial_age, gen.degradation_rate,
            gen.decommissioning_cost,
            gen.frequency_hz, gen.current_type,
            gen.reservoir_capacity, gen.reservoir_initial_level,
            gen.reservoir_min_level, gen.reservoir_max_level,
            gen.reservoir_inflow, gen.reservoir_turbine_efficiency,
            gen.reservoir_evaporation_rate, gen.reservoir_pump_capacity,
            gen.reservoir_pump_efficiency, gen.reservoir_spillage_allowed,
            gen.reservoir_invest_cost, gen.reservoir_invest_max,
            gen.risk_coefficient,
            gen.reservoir_min_release,
            gen.cascade_downstream, gen.cascade_delay_hours
        ))
    end

    # Copy and modify batteries
    modified_bats = Vector{BatteryConfig}()
    for bat in input.batteries
        new_invest_power = bat.invest_cost_power .* mult.invest_cost_storage
        new_invest_capacity = bat.invest_cost_capacity .* mult.invest_cost_storage
        new_maintenance_cost = bat.maintenance_cost .* mult.maintenance_cost

        push!(modified_bats, BatteryConfig(
            bat.name, bat.capacity,
            bat.max_charge_power, bat.max_discharge_power,
            bat.charge_efficiency, bat.discharge_efficiency,
            bat.soc_min, bat.soc_max, bat.soc_initial,
            bat.self_discharge,
            new_invest_power, new_invest_capacity,
            bat.invest_max_power, bat.invest_max_capacity,
            bat.life_time, bat.initial_age, bat.decommissioning_cost,
            bat.min_duration_hours, bat.max_duration_hours,
            new_maintenance_cost, bat.inertia, bat.spillage;
            degradation_rate = bat.degradation_rate,
            throughput_degradation_cost = bat.throughput_degradation_cost,
            risk_coefficient = bat.risk_coefficient
        ))
    end

    return modified_gens, modified_bats
end

"""
    build_stochastic_objective!(model, vars, input, scenarios)

Build stochastic objective: investment + expected operational costs.
"""
function build_stochastic_objective!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput,
    scenarios::Vector{Scenario}
)
    num_years = length(input.years)
    n_buses = input.network.num_buses
    b2n = input.network.bus_to_node
    n_gen = length(input.generators)
    n_bat = length(input.batteries)

    total_cost = AffExpr(0.0)

    # ── FIRST STAGE: Expected investment costs (scenario-weighted) ──
    # Investment decisions are first-stage (common across scenarios),
    # but unit costs are uncertain → weight by E[cost × probability].
    for scenario in scenarios
        prob = scenario.probability
        mult = scenario.multipliers
        scenario_discount = input.discount_rate * mult.discount_rate

        ypp = vars.years_per_inv_period
        # Investment costs — only at period starts
        for y_idx in 1:ypp:num_years
            df = 1.0 / ((1.0 + scenario_discount)^(y_idx - 1))
            w = prob * df  # scenario weight for this year

            # Technology investments (renewable vs conventional cost multiplier)
            n_tech = length(input.technologies)
            for t in 1:n_tech
                tech = input.technologies[t]
                is_re = tech.type == "Renewable"
                cost_mult = is_re ? mult.invest_cost_renewables : mult.invest_cost_conventional
                for n in 1:n_buses
                    if tech.invest_cost[n] > 0
                        add_to_expression!(total_cost,
                            vars.tech_investment[y_idx][t][n],
                            tech.invest_cost[n] * cost_mult * w)
                    end
                end
            end

            # Battery technology investments (storage cost multiplier)
            n_bat_tech = length(input.battery_technologies)
            for bt in 1:n_bat_tech
                bat_tech = input.battery_technologies[bt]
                for n in 1:n_buses
                    if bat_tech.invest_cost_power[n] > 0
                        add_to_expression!(total_cost,
                            vars.bat_tech_power_investment[y_idx][bt][n],
                            bat_tech.invest_cost_power[n] * mult.invest_cost_storage * w)
                    end
                    if bat_tech.invest_cost_capacity[n] > 0
                        add_to_expression!(total_cost,
                            vars.bat_tech_capacity_investment[y_idx][bt][n],
                            bat_tech.invest_cost_capacity[n] * mult.invest_cost_storage * w)
                    end
                end
            end

            # Transmission investments
            for ((i, j), var) in vars.transfer_investment[y_idx]
                trans_cost = input.network.transference_invest_cost[i]
                if trans_cost > 0
                    add_to_expression!(total_cost, var,
                        trans_cost * mult.invest_cost_transmission * w)
                end
            end
        end
    end

    # ── SECOND STAGE: Expected operational costs (scenario-weighted) ──
    # Operational decisions use shared representative-day variables.
    # Operational cost scales with scenario multipliers:
    #   - fuel_cost × fuel_price_growth^(y-1): fuel cost with year-over-year escalation
    #   - demand_growth^(y-1): more demand → proportionally more generation cost
    #   - carbon_price: scales CO2 cost component (when co2_cost > 0 in input)
    for scenario in scenarios
        prob = scenario.probability
        mult = scenario.multipliers
        scenario_discount = input.discount_rate * mult.discount_rate

        for y_idx in 1:num_years
            df = 1.0 / ((1.0 + scenario_discount)^(y_idx - 1))

            # Compound fuel price growth over the planning horizon
            fuel_escalation = mult.fuel_cost * (mult.fuel_price_growth ^ (y_idx - 1))
            # Demand growth scales operational costs (more demand → more generation)
            demand_scale = mult.demand_growth ^ (y_idx - 1)
            # Carbon price multiplier on CO2 component of operational cost
            carbon_mult = input.co2_cost > 0 ? mult.carbon_price : 1.0
            # Combined operational multiplier
            op_mult = fuel_escalation * demand_scale * carbon_mult

            if haskey(vars.operational_costs, y_idx)
                for day_cost in vars.operational_costs[y_idx]
                    add_to_expression!(total_cost, day_cost,
                        prob * df * op_mult)
                end
            end
        end
    end

    # ── Slack penalties (scenario-independent) ──
    for y_idx in 1:num_years
        add_to_expression!(total_cost, vars.slack_re_target[y_idx], input.slack_penalty)
        add_to_expression!(total_cost, vars.slack_budget[y_idx], input.slack_penalty)
        for ni in 1:input.network.num_nodes
            add_to_expression!(total_cost, vars.slack_capacity[(y_idx, ni)], input.slack_penalty)
        end
    end

    @objective(model, Min, total_cost)

    return total_cost  # Return for MGA near-optimal constraint
end

# =============================================================================
# Primary Energy Infrastructure Investment
# =============================================================================

"""
    add_primary_energy_investment_variables!(model, ext_vars, input, pe_configs)

Add primary energy infrastructure investment variables to the model.
"""
function add_primary_energy_investment_variables!(
    model::Model,
    ext_vars::ExtendedMasterVariables,
    input::MasterProblemInput,
    pe_configs::Vector{PrimaryEnergyInvestmentConfig}
)
    num_years = length(input.years)
    n_buses = input.network.num_buses

    for pe_config in pe_configs
        fuel_id = pe_config.fuel_id

        # Storage investment per node per year
        ext_vars.fuel_storage_investment[fuel_id] = Dict{Int, Vector{VariableRef}}()
        for y_idx in 1:num_years
            ext_vars.fuel_storage_investment[fuel_id][y_idx] = @variable(model,
                [n=1:n_buses],
                lower_bound = 0,
                upper_bound = pe_config.storage_invest_max[n],
                base_name = "fuel_stor_inv_$(fuel_id)_y$(y_idx)"
            )
        end

        # Transport investment per route per year
        ext_vars.fuel_transport_investment[fuel_id] = Dict{Int, Dict{Int, VariableRef}}()
        for y_idx in 1:num_years
            ext_vars.fuel_transport_investment[fuel_id][y_idx] = Dict{Int, VariableRef}()
            for (r, route) in enumerate(input.transport_routes)
                if haskey(route.fuel_params, fuel_id)
                    ext_vars.fuel_transport_investment[fuel_id][y_idx][r] = @variable(model,
                        lower_bound = 0,
                        upper_bound = pe_config.transport_invest_max,
                        base_name = "fuel_trans_inv_$(fuel_id)_y$(y_idx)_r$(r)"
                    )
                end
            end
        end
    end
end

"""
    add_primary_energy_investment_costs!(total_cost, ext_vars, input, pe_configs, discount_factor, y_idx)

Add primary energy investment costs to objective function.
"""
function add_primary_energy_investment_costs!(
    total_cost::AffExpr,
    ext_vars::ExtendedMasterVariables,
    input::MasterProblemInput,
    pe_configs::Vector{PrimaryEnergyInvestmentConfig},
    discount_factor::Float64,
    y_idx::Int
)
    n_buses = input.network.num_buses

    for pe_config in pe_configs
        fuel_id = pe_config.fuel_id

        # Storage investment cost
        if haskey(ext_vars.fuel_storage_investment, fuel_id)
            for n in 1:n_buses
                add_to_expression!(total_cost,
                    ext_vars.fuel_storage_investment[fuel_id][y_idx][n],
                    pe_config.storage_invest_cost[n] * discount_factor)
            end
        end

        # Transport investment cost (route-based, includes distance factor)
        if haskey(ext_vars.fuel_transport_investment, fuel_id)
            for (r, var) in ext_vars.fuel_transport_investment[fuel_id][y_idx]
                route = input.transport_routes[r]
                transport_cost = pe_config.transport_invest_cost * route.distance_km
                add_to_expression!(total_cost, var, transport_cost * discount_factor)
            end
        end
    end
end

# =============================================================================
# NPV Iteration for Unit Retirement
# =============================================================================

"""
    calculate_unit_npv(
        result::MasterProblemResult,
        input::MasterProblemInput,
        unit_type::String,
        unit_idx::Int,
        node::Int
    )

Calculate NPV for a specific unit.

Returns UnitNPV with recommendation for retirement.
"""
function calculate_unit_npv(
    result::MasterProblemResult,
    input::MasterProblemInput,
    unit_type::String,
    unit_idx::Int,
    node::Int;
    npv_threshold::Float64 = 0.0
)::UnitNPV
    num_years = length(input.years)

    if unit_type == "generator"
        gen = input.generators[unit_idx]
        rated_power = gen.rated_power[node]
        lifetime = gen.life_time[node]
        initial_age = gen.initial_age[node]
        remaining_life = max(0.0, lifetime - initial_age)

        # Capacity factor from availability (mean availability × efficiency)
        cf = 1.0  # dispatchable default
        if size(gen.availability, 1) > 0 && node <= size(gen.availability, 2)
            cf = sum(gen.availability[:, node]) / size(gen.availability, 1)
        end
        cf *= gen.eff_at_rated[node]

        # Annual generation (MWh)
        hours_year = length(input.hours_per_year) > 0 ? Float64(input.hours_per_year[1]) : Float64(HOURS_STD_YEAR)
        annual_generation = rated_power * cf * hours_year

        # Annual costs ($/year) — fixed_cost and maintenance_cost are in $/MWh
        annual_fuel = annual_generation * gen.fuel_cost[node]
        annual_fixed = annual_generation * gen.fixed_cost[node]
        annual_maintenance = annual_generation * gen.maintenance_cost[node]
        total_annual_cost = annual_fuel + annual_fixed + annual_maintenance

        # Annual revenue: generation × base LCOE
        annual_revenue = annual_generation * input.base_lcoe

        # NPV over remaining life with degradation
        degradation_rate = gen.degradation_rate[node]
        npv = 0.0
        for y in 1:min(Int(ceil(remaining_life)), num_years)
            deg_factor = (1.0 - degradation_rate) ^ y
            net_cf = (annual_revenue - total_annual_cost) * deg_factor
            npv += net_cf / ((1.0 + input.discount_rate) ^ y)
        end

        # Subtract decommissioning cost (present value at end of life)
        decom = gen.decommissioning_cost[node] * rated_power
        if decom > 0 && remaining_life > 0
            npv -= decom / ((1.0 + input.discount_rate) ^ remaining_life)
        end

        recommend_retire = npv < npv_threshold

        return UnitNPV(unit_type, unit_idx, node, input.system_name,
                      npv, remaining_life, recommend_retire)

    elseif unit_type == "battery"
        bat = input.batteries[unit_idx]
        rated_power = bat.max_discharge_power[node]
        invest_cost = bat.invest_cost_power[node]
        lifetime = bat.life_time[node]
        initial_age = bat.initial_age[node]
        remaining_life = max(0.0, lifetime - initial_age)

        # Revenue from arbitrage (npv_annual_return_rate × invest_cost as proxy)
        annual_revenue = rated_power * invest_cost * input.npv_annual_return_rate

        # Maintenance cost from fixed_cost field ($/MWh × hours)
        hours_year = length(input.hours_per_year) > 0 ? Float64(input.hours_per_year[1]) : Float64(HOURS_STD_YEAR)
        annual_cost = rated_power * bat.fixed_cost[node] * hours_year

        # NPV with degradation
        degradation_rate = bat.degradation_rate[node]
        npv = 0.0
        for y in 1:min(Int(ceil(remaining_life)), num_years)
            deg_factor = (1.0 - degradation_rate) ^ y
            npv += (annual_revenue - annual_cost) * deg_factor / ((1.0 + input.discount_rate) ^ y)
        end

        # Decommissioning cost
        decom = bat.decommissioning_cost[node] * rated_power
        if decom > 0 && remaining_life > 0
            npv -= decom / ((1.0 + input.discount_rate) ^ remaining_life)
        end

        recommend_retire = npv < npv_threshold

        return UnitNPV(unit_type, unit_idx, node, input.system_name,
                      npv, remaining_life, recommend_retire)
    else
        error("Unknown unit type: $unit_type")
    end
end

"""
    get_units_with_negative_npv(
        result::MasterProblemResult,
        input::MasterProblemInput;
        npv_threshold::Float64 = 0.0
    )

Identify all units with NPV below threshold.
"""
function get_units_with_negative_npv(
    result::MasterProblemResult,
    input::MasterProblemInput;
    npv_threshold::Float64 = 0.0
)::Vector{UnitNPV}
    negative_npv_units = UnitNPV[]
    n_buses = input.network.num_buses
    b2n = input.network.bus_to_node

    # Check generators
    for (g, gen) in enumerate(input.generators)
        for n in 1:n_buses
            if gen.rated_power[n] > 0
                unit_npv = calculate_unit_npv(result, input, "generator", g, n;
                                             npv_threshold=npv_threshold)
                if unit_npv.recommend_retirement
                    push!(negative_npv_units, unit_npv)
                end
            end
        end
    end

    # Check batteries
    for (b, bat) in enumerate(input.batteries)
        for n in 1:n_buses
            if bat.max_discharge_power[n] > 0
                unit_npv = calculate_unit_npv(result, input, "battery", b, n;
                                             npv_threshold=npv_threshold)
                if unit_npv.recommend_retirement
                    push!(negative_npv_units, unit_npv)
                end
            end
        end
    end

    return negative_npv_units
end

"""
    force_unit_retirements!(model, vars, units_to_retire::Vector{UnitNPV})

No-op: retirement is now handled by automatic expiry at lifetime.
Kept for interface compatibility.
"""
function force_unit_retirements!(
    model::Model,
    vars::MasterProblemVariables,
    units_to_retire::Vector{UnitNPV}
)
    # No-op: unified retirement logic handles capacity expiry at lifetime
    return nothing
end

"""
    solve_with_npv_iteration(
        input::MasterProblemInput;
        max_iterations::Int = 5,
        npv_threshold::Float64 = 0.0,
        use_representative_days::Bool = true
    )

Solve Master Problem with iterative NPV-based retirement.

1. Solve initial problem
2. Calculate NPV for all units
3. Force retirement of negative NPV units
4. Re-solve
5. Repeat until convergence or max iterations
"""
function solve_with_npv_iteration(
    input::MasterProblemInput;
    max_iterations::Int = 5,
    npv_threshold::Float64 = 0.0,
    use_representative_days::Bool = true
)::NPVIterationResult
    all_forced_retirements = UnitNPV[]
    npv_history = Dict{String, Float64}[]

    # Initial solve
    model, vars, targets = create_master_problem(input; use_representative_days=use_representative_days)
    optimize!(model)

    if termination_status(model) != MOI.OPTIMAL &&
       termination_status(model) != MOI.LOCALLY_SOLVED
        # Return with failure
        result = extract_master_solution(model, vars, input)
        return NPVIterationResult(1, false, result, all_forced_retirements, npv_history)
    end

    result = extract_master_solution(model, vars, input)

    for iter in 1:max_iterations
        # Calculate NPV for all units
        negative_npv = get_units_with_negative_npv(result, input; npv_threshold=npv_threshold)

        # Track NPV history
        iter_npv = Dict{String, Float64}()
        for unit in negative_npv
            key = "$(unit.unit_type)_$(unit.unit_idx)_$(unit.node)"
            iter_npv[key] = unit.npv
        end
        push!(npv_history, iter_npv)

        if isempty(negative_npv)
            # Converged - no more negative NPV units
            return NPVIterationResult(iter, true, result, all_forced_retirements, npv_history)
        end

        # Force retirements
        append!(all_forced_retirements, negative_npv)
        force_unit_retirements!(model, vars, negative_npv)

        # Re-solve
        optimize!(model)

        if termination_status(model) != MOI.OPTIMAL &&
           termination_status(model) != MOI.LOCALLY_SOLVED
            return NPVIterationResult(iter, false, result, all_forced_retirements, npv_history)
        end

        result = extract_master_solution(model, vars, input)
    end

    # Max iterations reached
    return NPVIterationResult(max_iterations, false, result, all_forced_retirements, npv_history)
end

# =============================================================================
# Infeasibility Diagnostics
# =============================================================================

"""
    diagnose_infeasibility(model::Model, vars::MasterProblemVariables, input::MasterProblemInput)

Diagnose model infeasibility by checking slack variable values.

Returns Dict with constraint category => violation amount.
"""
function diagnose_infeasibility(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput
)::Dict{String, Float64}
    violations = Dict{String, Float64}()

    num_years = length(input.years)
    n_buses = input.network.num_buses

    # Check if model is solved
    status = termination_status(model)
    if status == MOI.OPTIMAL || status == MOI.LOCALLY_SOLVED
        # Check slack values
        total_re_slack = 0.0
        total_budget_slack = 0.0
        total_capacity_slack = 0.0

        for y_idx in 1:num_years
            re_slack_val = value(vars.slack_re_target[y_idx])
            if re_slack_val > 1e-6
                total_re_slack += re_slack_val
                violations["re_target_y$(y_idx)"] = re_slack_val
            end

            budget_slack_val = value(vars.slack_budget[y_idx])
            if budget_slack_val > 1e-6
                total_budget_slack += budget_slack_val
                violations["budget_y$(y_idx)"] = budget_slack_val
            end

            for ni in 1:input.network.num_nodes
                cap_slack_val = value(vars.slack_capacity[(y_idx, ni)])
                if cap_slack_val > 1e-6
                    total_capacity_slack += cap_slack_val
                    violations["capacity_y$(y_idx)_node$(ni)"] = cap_slack_val
                end
            end
        end

        violations["total_re_slack"] = total_re_slack
        violations["total_budget_slack"] = total_budget_slack
        violations["total_capacity_slack"] = total_capacity_slack
    else
        violations["model_status"] = -1.0
        violations["status_code"] = Float64(Int(status))
    end

    return violations
end

"""
    log_solution_summary(result::MasterProblemResult, input::MasterProblemInput; ev_demand::Union{Dict{Int, Float64}, Nothing}=nothing)

Generate a formatted solution summary string with detailed debug information.

Args:
    result: MasterProblemResult with optimization results
    input: MasterProblemInput with problem configuration
    ev_demand: Optional dict mapping year_idx => total EV demand (MWh) for that year
"""
function log_solution_summary(
    result::MasterProblemResult,
    input::MasterProblemInput;
    ev_demand::Union{Dict{Int, Float64}, Nothing}=nothing
)::String
    lines = String[]
    num_years = length(input.years)
    n_buses = input.network.num_buses
    b2n = input.network.bus_to_node
    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    system_name = input.system_name

    push!(lines, "=" ^ 100)
    push!(lines, "MASTER PROBLEM SOLUTION SUMMARY - SYSTEM: $(uppercase(system_name))")
    push!(lines, "=" ^ 100)

    # ==========================================================================
    # Basic Status
    # ==========================================================================
    push!(lines, "\n[STATUS - $(system_name)]")
    push!(lines, "  Termination Status: $(result.status)")
    push!(lines, "  Total Objective (NPV): \$$(round(result.objective, digits=2)) M")
    push!(lines, "  Solve Time: $(round(result.solve_time, digits=1)) s")

    # ==========================================================================
    # DEMAND AND RE TARGET SUMMARY TABLE (Years in rows, Demand/Target in columns)
    # ==========================================================================
    push!(lines, "\n" * "=" ^ 100)
    push!(lines, "[DEMAND AND RE TARGET SUMMARY - $(system_name)]")
    push!(lines, "-" ^ 100)

    targets = calculate_target_ratios(input)
    base_demand_total = sum(input.base_demand)
    hours = size(input.base_demand, 1)

    # Table header
    push!(lines, "┌─────────┬──────────────────┬──────────────────┬──────────────────┬────────────┬────────────┬────────────┐")
    push!(lines, "│  Year   │  Base Demand     │  EV Demand       │  Total Demand    │ Target RE% │Achieved RE%│   Diff     │")
    push!(lines, "│         │     (GWh)        │     (GWh)        │     (GWh)        │            │            │            │")
    push!(lines, "├─────────┼──────────────────┼──────────────────┼──────────────────┼────────────┼────────────┼────────────┤")

    for y_idx in 1:num_years
        year = input.years[y_idx]
        growth_factor = (1 + input.demand_growth) ^ (y_idx - 1)
        base_dem_gwh = base_demand_total * growth_factor / 1000.0  # Convert MWh to GWh

        # EV demand for this year (if provided)
        ev_dem_gwh = if ev_demand !== nothing && haskey(ev_demand, y_idx)
            ev_demand[y_idx] / 1000.0
        else
            0.0
        end

        total_dem_gwh = base_dem_gwh + ev_dem_gwh

        target_pct = targets[(1, y_idx)] * 100
        achieved_pct = result.re_penetration_by_year[y_idx] * 100
        diff_pct = achieved_pct - target_pct
        diff_str = diff_pct >= 0 ? "+$(round(diff_pct, digits=1))" : "$(round(diff_pct, digits=1))"

        push!(lines, "│ $(rpad(year, 7)) │ $(lpad(round(base_dem_gwh, digits=1), 16)) │ $(lpad(round(ev_dem_gwh, digits=1), 16)) │ $(lpad(round(total_dem_gwh, digits=1), 16)) │ $(lpad(round(target_pct, digits=1), 10)) │ $(lpad(round(achieved_pct, digits=1), 10)) │ $(lpad(diff_str, 10)) │")
    end
    push!(lines, "└─────────┴──────────────────┴──────────────────┴──────────────────┴────────────┴────────────┴────────────┘")

    # ==========================================================================
    # Installed Capacity (Initial) - TABLE FORMAT (Nodes in rows, Technologies in columns)
    # ==========================================================================
    push!(lines, "\n" * "=" ^ 100)
    push!(lines, "[INITIAL INSTALLED CAPACITY - $(system_name)]")
    push!(lines, "-" ^ 100)

    # Collect all technology names
    gen_names = [input.generators[g].name for g in 1:n_gen]
    bat_names = [input.batteries[b].name for b in 1:n_bat]

    if n_gen > 0 || n_bat > 0
        col_width = max(12, maximum(vcat(length.(gen_names), length.(bat_names), [8])) + 2)

        # Header row for generators
        push!(lines, "\n  GENERATORS (MW):")
        header = "    " * rpad("Node", 10)
        for name in gen_names
            header *= rpad(name, col_width)
        end
        header *= rpad("TOTAL", col_width)
        push!(lines, header)
        push!(lines, "    " * "-" ^ (10 + col_width * (n_gen + 1)))

        # Data rows for generators
        node_totals = zeros(n_buses)
        for n in 1:n_buses
            row = "    " * rpad("Node $n", 10)
            node_total = 0.0
            for g in 1:n_gen
                gen = input.generators[g]
                val = n <= length(gen.rated_power) ? gen.rated_power[n] : 0.0
                node_total += val
                row *= rpad(val > 0 ? "$(round(val, digits=1))" : "-", col_width)
            end
            node_totals[n] = node_total
            row *= rpad("$(round(node_total, digits=1))", col_width)
            push!(lines, row)
        end

        # Total row for generators
        push!(lines, "    " * "-" ^ (10 + col_width * (n_gen + 1)))
        total_row = "    " * rpad("TOTAL", 10)
        for g in 1:n_gen
            total_row *= rpad("$(round(sum(input.generators[g].rated_power), digits=1))", col_width)
        end
        total_row *= rpad("$(round(sum(node_totals), digits=1))", col_width)
        push!(lines, total_row)

        # Battery table
        if n_bat > 0
            push!(lines, "\n  BATTERIES (MW / MWh):")
            header = "    " * rpad("Node", 10)
            for name in bat_names
                header *= rpad(name * " MW", col_width)
                header *= rpad(name * " MWh", col_width)
            end
            push!(lines, header)
            push!(lines, "    " * "-" ^ (10 + col_width * 2 * n_bat))

            for n in 1:n_buses
                row = "    " * rpad("Node $n", 10)
                for b in 1:n_bat
                    bat = input.batteries[b]
                    pow = n <= length(bat.max_discharge_power) ? bat.max_discharge_power[n] : 0.0
                    cap = n <= length(bat.capacity) ? bat.capacity[n] : 0.0
                    row *= rpad(pow > 0 ? "$(round(pow, digits=1))" : "-", col_width)
                    row *= rpad(cap > 0 ? "$(round(cap, digits=1))" : "-", col_width)
                end
                push!(lines, row)
            end

            # Total row for batteries
            push!(lines, "    " * "-" ^ (10 + col_width * 2 * n_bat))
            total_row = "    " * rpad("TOTAL", 10)
            for b in 1:n_bat
                total_row *= rpad("$(round(sum(input.batteries[b].max_discharge_power), digits=1))", col_width)
                total_row *= rpad("$(round(sum(input.batteries[b].capacity), digits=1))", col_width)
            end
            push!(lines, total_row)
        end
    end

    # Summary
    renewable_capacity = n_gen > 0 ? sum(sum(input.generators[g].rated_power) for g in 1:n_gen if input.generators[g].type == "Renewable"; init=0.0) : 0.0
    conventional_capacity = n_gen > 0 ? sum(sum(input.generators[g].rated_power) for g in 1:n_gen if input.generators[g].type != "Renewable"; init=0.0) : 0.0
    total_bat_power = n_bat > 0 ? sum(sum(input.batteries[b].max_discharge_power) for b in 1:n_bat) : 0.0
    total_bat_energy = n_bat > 0 ? sum(sum(input.batteries[b].capacity) for b in 1:n_bat) : 0.0

    push!(lines, "\n  SUMMARY:")
    push!(lines, "    Renewable: $(round(renewable_capacity, digits=1)) MW")
    push!(lines, "    Conventional: $(round(conventional_capacity, digits=1)) MW")
    push!(lines, "    Battery: $(round(total_bat_power, digits=1)) MW / $(round(total_bat_energy, digits=1)) MWh")

    # ==========================================================================
    # Cumulative Capacity by Year (including investments)
    # ==========================================================================
    push!(lines, "\n" * "=" ^ 100)
    push!(lines, "[CUMULATIVE RENEWABLE CAPACITY BY YEAR - $(system_name)]")
    push!(lines, "-" ^ 100)
    push!(lines, "  Year    Existing RE      RE Invested      Total RE (MW)")
    push!(lines, "-" ^ 60)

    n_tech = length(input.technologies)
    for y_idx in 1:num_years
        year = input.years[y_idx]
        existing_re = 0.0
        invested_re = 0.0

        for g in 1:n_gen
            gen = input.generators[g]
            if gen.type == "Renewable"
                existing_re += sum(gen.rated_power)
            end
        end

        # Cumulative technology investment up to this year
        for t in 1:n_tech
            tech = input.technologies[t]
            if tech.type == "Renewable"
                for prev_y in 1:y_idx
                    if haskey(result.tech_investment, prev_y) && haskey(result.tech_investment[prev_y], t)
                        invested_re += sum(result.tech_investment[prev_y][t])
                    end
                end
            end
        end

        total_re = existing_re + invested_re
        push!(lines, "  $(rpad(year, 6))  $(rpad(round(existing_re, digits=1), 15))  $(rpad(round(invested_re, digits=1), 15))  $(round(total_re, digits=1))")
    end

    # ==========================================================================
    # Investment Details by Year - TABLE FORMAT (Nodes in rows, Technologies in columns)
    # ==========================================================================
    push!(lines, "\n" * "=" ^ 100)
    push!(lines, "[INVESTMENT DETAILS BY YEAR - $(system_name)]")
    push!(lines, "-" ^ 100)

    for y_idx in 1:num_years
        year = input.years[y_idx]
        total_inv = result.total_investment_by_year[y_idx]
        push!(lines, "\n  Year $(year): Total Investment = \$$(round(total_inv, digits=2)) M")

        # Collect all technologies with investments this year
        tech_names = String[]
        tech_indices = Int[]
        for t in 1:n_tech
            if haskey(result.tech_investment[y_idx], t) && sum(result.tech_investment[y_idx][t]) > 0.1
                push!(tech_names, input.technologies[t].name)
                push!(tech_indices, t)
            end
        end

        bat_tech_names = String[]
        bat_tech_indices = Int[]
        n_bat_tech = length(input.battery_technologies)
        for bt in 1:n_bat_tech
            has_pow = haskey(result.bat_tech_power_investment[y_idx], bt) && sum(result.bat_tech_power_investment[y_idx][bt]) > 0.1
            has_cap = haskey(result.bat_tech_capacity_investment[y_idx], bt) && sum(result.bat_tech_capacity_investment[y_idx][bt]) > 0.1
            if has_pow || has_cap
                push!(bat_tech_names, input.battery_technologies[bt].name)
                push!(bat_tech_indices, bt)
            end
        end

        if isempty(tech_names) && isempty(bat_tech_names)
            push!(lines, "    No investments this year")
            continue
        end

        # Build header with technology names (columns)
        all_tech_labels = vcat(tech_names, [n * " (MW)" for n in bat_tech_names], [n * " (MWh)" for n in bat_tech_names])
        col_width = max(12, maximum(length.(all_tech_labels)) + 2)

        # Header row
        header = "    " * rpad("Node", 10)
        for name in tech_names
            header *= rpad(name, col_width)
        end
        for name in bat_tech_names
            header *= rpad(name * " (MW)", col_width)
        end
        for name in bat_tech_names
            header *= rpad(name * " (MWh)", col_width)
        end
        push!(lines, header)
        push!(lines, "    " * "-" ^ (10 + col_width * (length(tech_names) + 2 * length(bat_tech_names))))

        # Data rows (one per node)
        for n in 1:n_buses
            row = "    " * rpad("Node $n", 10)

            # Technology investments for this node
            for t in tech_indices
                tech_inv = result.tech_investment[y_idx][t]
                val = length(tech_inv) >= n ? tech_inv[n] : 0.0
                row *= rpad(val > 0.1 ? "$(round(val, digits=1))" : "-", col_width)
            end

            # Battery technology power investments for this node
            for bt in bat_tech_indices
                bat_pow = result.bat_tech_power_investment[y_idx][bt]
                val = length(bat_pow) >= n ? bat_pow[n] : 0.0
                row *= rpad(val > 0.1 ? "$(round(val, digits=1))" : "-", col_width)
            end

            # Battery technology capacity investments for this node
            for bt in bat_tech_indices
                bat_cap = result.bat_tech_capacity_investment[y_idx][bt]
                val = length(bat_cap) >= n ? bat_cap[n] : 0.0
                row *= rpad(val > 0.1 ? "$(round(val, digits=1))" : "-", col_width)
            end

            push!(lines, row)
        end

        # Total row
        total_row = "    " * rpad("TOTAL", 10)
        for t in tech_indices
            total_row *= rpad("$(round(sum(result.tech_investment[y_idx][t]), digits=1))", col_width)
        end
        for bt in bat_tech_indices
            total_row *= rpad("$(round(sum(result.bat_tech_power_investment[y_idx][bt]), digits=1))", col_width)
        end
        for bt in bat_tech_indices
            total_row *= rpad("$(round(sum(result.bat_tech_capacity_investment[y_idx][bt]), digits=1))", col_width)
        end
        push!(lines, "    " * "-" ^ (10 + col_width * (length(tech_names) + 2 * length(bat_tech_names))))
        push!(lines, total_row)
    end

    # ==========================================================================
    # Operational Cost by Year
    # ==========================================================================
    push!(lines, "\n" * "=" ^ 100)
    push!(lines, "[OPERATIONAL COST BY YEAR - $(system_name)]")
    push!(lines, "-" ^ 100)
    push!(lines, "  Year    Operational Cost     Cumulative NPV")
    push!(lines, "-" ^ 50)
    cumulative_npv = 0.0
    for y_idx in 1:num_years
        year = input.years[y_idx]
        op_cost = result.total_operational_cost_by_year[y_idx]
        discount_factor = 1.0 / ((1.0 + input.discount_rate) ^ (y_idx - 1))
        discounted = op_cost * discount_factor
        cumulative_npv += discounted + result.total_investment_by_year[y_idx] * discount_factor
        push!(lines, "  $(rpad(year, 6))  \$$(rpad(round(op_cost, digits=2), 18))M  \$$(round(cumulative_npv, digits=2))M")
    end

    # ==========================================================================
    # Retirement Summary (unified: capacity expires at lifetime)
    # ==========================================================================
    push!(lines, "\n" * "=" ^ 100)
    push!(lines, "[RETIREMENT STATUS - $(system_name)]")
    push!(lines, "-" ^ 100)

    # Collect generators with capacity that will retire during the horizon
    retirement_gens = Set{Int}()
    for g in 1:n_gen
        gen = input.generators[g]
        for n in 1:n_buses
            if gen.rated_power[n] > 0
                initial_age = gen.initial_age[n]
                lifetime = gen.life_time[n]
                # Check if unit retires during the planning horizon
                for y_idx in 1:num_years
                    age = initial_age + (y_idx - 1)
                    if age >= lifetime
                        push!(retirement_gens, g)
                        break
                    end
                end
            end
        end
    end

    if isempty(retirement_gens)
        push!(lines, "  No retirements during planning horizon")
    else
        # Collect tech names
        tech_names = String[]
        tech_indices = Int[]
        for g in sort(collect(retirement_gens))
            push!(tech_names, input.generators[g].name)
            push!(tech_indices, g)
        end

        col_width = max(15, maximum(length.(tech_names)) + 2)

        # Header
        header = "    " * rpad("Year", 10)
        for name in tech_names
            header *= rpad(name, col_width)
        end
        push!(lines, header)
        push!(lines, "    " * "-" ^ (10 + col_width * length(tech_names)))

        # One row per year showing status
        for y_idx in 1:num_years
            year = input.years[y_idx]
            row = "    " * rpad("$(year)", 10)
            for g in tech_indices
                gen = input.generators[g]
                # Sum capacity across nodes for this tech
                active_cap = 0.0
                retired_cap = 0.0
                for n in 1:n_buses
                    if gen.rated_power[n] > 0
                        age = gen.initial_age[n] + (y_idx - 1)
                        if age < gen.life_time[n]
                            deg = (1.0 - gen.degradation_rate[n]) ^ age
                            active_cap += gen.rated_power[n] * deg
                        else
                            retired_cap += gen.rated_power[n]
                        end
                    end
                end
                if retired_cap > 0 && active_cap == 0
                    row *= rpad("RETIRED", col_width)
                elseif retired_cap > 0
                    row *= rpad("PARTIAL", col_width)
                else
                    row *= rpad("ACTIVE", col_width)
                end
            end
            push!(lines, row)
        end
    end

    # ==========================================================================
    # Investment Retirement Summary
    # ==========================================================================
    push!(lines, "\n" * "-" ^ 100)
    push!(lines, "[INVESTMENT RETIREMENT STATUS - $(system_name)]")
    push!(lines, "-" ^ 100)

    # Collect investments that will retire during the horizon
    investment_retirements = Vector{NamedTuple{(:tech, :inv_year, :retire_year, :capacity, :type), Tuple{String, Int, Int, Float64, String}}}()

    # Technology investments
    for t in 1:n_tech
        tech = input.technologies[t]
        for inv_year_idx in 1:num_years
            inv_year = input.years[inv_year_idx]
            for n in 1:n_buses
                if haskey(result.tech_investment[inv_year_idx], t)
                    inv_cap = result.tech_investment[inv_year_idx][t][n]
                    if inv_cap > 0.01  # Threshold to avoid numerical noise
                        lifetime = tech.life_time[n]
                        retire_year_idx = inv_year_idx + Int(floor(lifetime))
                        if retire_year_idx <= num_years
                            retire_year = input.years[retire_year_idx]
                            push!(investment_retirements, (
                                tech = tech.name,
                                inv_year = inv_year,
                                retire_year = retire_year,
                                capacity = inv_cap,
                                type = "technology"
                            ))
                        end
                    end
                end
            end
        end
    end

    # Battery technology investments
    n_bat_tech = length(input.battery_technologies)
    for bt in 1:n_bat_tech
        bat_tech = input.battery_technologies[bt]
        for inv_year_idx in 1:num_years
            inv_year = input.years[inv_year_idx]
            for n in 1:n_buses
                if haskey(result.bat_tech_capacity_investment[inv_year_idx], bt)
                    inv_cap = result.bat_tech_capacity_investment[inv_year_idx][bt][n]
                    if inv_cap > 0.01  # Threshold to avoid numerical noise
                        lifetime = bat_tech.life_time[n]
                        retire_year_idx = inv_year_idx + Int(floor(lifetime))
                        if retire_year_idx <= num_years
                            retire_year = input.years[retire_year_idx]
                            push!(investment_retirements, (
                                tech = bat_tech.name,
                                inv_year = inv_year,
                                retire_year = retire_year,
                                capacity = inv_cap,
                                type = "battery_technology"
                            ))
                        end
                    end
                end
            end
        end
    end

    if isempty(investment_retirements)
        push!(lines, "  No investment retirements during planning horizon")
        push!(lines, "  (Investments made will outlive the simulation period)")
    else
        # Sort by retirement year
        sort!(investment_retirements, by = x -> (x.retire_year, x.inv_year))

        # Header
        push!(lines, "    " * rpad("Technology", 20) * rpad("Invested", 12) * rpad("Retires", 12) * rpad("Capacity", 15) * "Type")
        push!(lines, "    " * "-" ^ 70)

        for ret in investment_retirements
            cap_str = ret.type == "battery" ? "$(round(ret.capacity, digits=1)) MWh" : "$(round(ret.capacity, digits=1)) MW"
            push!(lines, "    " * rpad(ret.tech, 20) * rpad("$(ret.inv_year)", 12) * rpad("$(ret.retire_year)", 12) * rpad(cap_str, 15) * ret.type)
        end

        # Summary by year
        push!(lines, "\n    Retirement summary by year:")
        years_with_retirements = unique([r.retire_year for r in investment_retirements])
        for yr in sort(years_with_retirements)
            yr_retirements = filter(r -> r.retire_year == yr, investment_retirements)
            gen_cap = sum([r.capacity for r in yr_retirements if r.type == "generator"], init=0.0)
            bat_cap = sum([r.capacity for r in yr_retirements if r.type == "battery"], init=0.0)
            summary = "      $(yr): "
            parts = String[]
            if gen_cap > 0
                push!(parts, "$(round(gen_cap, digits=1)) MW generators")
            end
            if bat_cap > 0
                push!(parts, "$(round(bat_cap, digits=1)) MWh batteries")
            end
            push!(lines, summary * join(parts, ", "))
        end
    end

    # ==========================================================================
    # Debug: Objective Function Components
    # ==========================================================================
    push!(lines, "\n" * "=" ^ 100)
    push!(lines, "[OBJECTIVE FUNCTION BREAKDOWN - $(system_name)]")
    push!(lines, "-" ^ 100)
    total_investment = sum(result.total_investment_by_year)
    total_operational = sum(result.total_operational_cost_by_year)
    push!(lines, "  Total Investment (undiscounted): \$$(round(total_investment, digits=2)) M")
    push!(lines, "  Total Operational (undiscounted): \$$(round(total_operational, digits=2)) M")
    push!(lines, "  NPV Objective: \$$(round(result.objective, digits=2)) M")

    # Verify NPV calculation
    calculated_npv = 0.0
    for y_idx in 1:num_years
        df = 1.0 / ((1.0 + input.discount_rate) ^ (y_idx - 1))
        calculated_npv += (result.total_investment_by_year[y_idx] + result.total_operational_cost_by_year[y_idx]) * df
    end
    push!(lines, "  Calculated NPV (verification): \$$(round(calculated_npv, digits=2)) M")
    npv_diff = abs(calculated_npv - result.objective) / max(1.0, result.objective) * 100
    if npv_diff > 1.0
        push!(lines, "  [WARNING] NPV discrepancy: $(round(npv_diff, digits=2))%")
    end

    push!(lines, "\n" * "=" ^ 100)
    push!(lines, "END OF SOLUTION SUMMARY FOR SYSTEM: $(uppercase(system_name))")
    push!(lines, "=" ^ 100)

    return join(lines, "\n")
end

# =============================================================================
# Solution Export
# =============================================================================

"""
    export_solution_to_dict(result::MasterProblemResult, input::MasterProblemInput)

Export solution to a dictionary for JSON/HDF5 serialization.
"""
function export_solution_to_dict(
    result::MasterProblemResult,
    input::MasterProblemInput
)::Dict{String, Any}
    export_dict = Dict{String, Any}()

    export_dict["status"] = string(result.status)
    export_dict["objective"] = result.objective
    export_dict["solve_time"] = result.solve_time
    export_dict["years"] = input.years

    # Technology investment decisions
    tech_inv_export = Dict{String, Any}()
    for (y_idx, tech_dict) in result.tech_investment
        year_str = string(input.years[y_idx])
        tech_inv_export[year_str] = Dict{String, Vector{Float64}}()
        for (t, vals) in tech_dict
            tech_inv_export[year_str][input.technologies[t].name] = vals
        end
    end
    export_dict["tech_investment"] = tech_inv_export

    # Battery technology investment decisions
    bat_tech_pow_export = Dict{String, Any}()
    bat_tech_cap_export = Dict{String, Any}()
    for (y_idx, bt_dict) in result.bat_tech_power_investment
        year_str = string(input.years[y_idx])
        bat_tech_pow_export[year_str] = Dict{String, Vector{Float64}}()
        bat_tech_cap_export[year_str] = Dict{String, Vector{Float64}}()
        for (bt, vals) in bt_dict
            bat_tech_pow_export[year_str][input.battery_technologies[bt].name] = vals
            bat_tech_cap_export[year_str][input.battery_technologies[bt].name] = result.bat_tech_capacity_investment[y_idx][bt]
        end
    end
    export_dict["bat_tech_power_investment"] = bat_tech_pow_export
    export_dict["bat_tech_capacity_investment"] = bat_tech_cap_export

    # Retirement status (1.0 = active, 0.0 = retired at lifetime)
    gen_life_export = Dict{String, Any}()
    for (y_idx, gen_dict) in result.gen_life_extension
        year_str = string(input.years[y_idx])
        gen_life_export[year_str] = Dict{String, Vector{Float64}}()
        for (g, vals) in gen_dict
            gen_life_export[year_str][input.generators[g].name] = vals
        end
    end
    export_dict["retirement_status"] = gen_life_export

    # Summary metrics
    export_dict["total_investment_by_year"] = result.total_investment_by_year
    export_dict["total_operational_cost_by_year"] = result.total_operational_cost_by_year
    export_dict["re_penetration_by_year"] = result.re_penetration_by_year
    export_dict["re_penetration_by_system"] = Dict{String, Any}(
        k => collect(v) for (k, v) in result.re_penetration_by_system
    )

    return export_dict
end
