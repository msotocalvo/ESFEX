"""
benders.jl - Benders Decomposition for Master Problem

Decomposes the monolithic capacity expansion problem into:
- Master: investment decisions + recourse variables θ[y]
- Subproblems: operational dispatch per (year, representative day) with fixed capacities

Algorithm:
1. Create Benders master (investment-only)
2. Iterate:
   a. Solve master → x*, θ*; LB = objective
   b. Fix capacities from x*, solve all subproblems → Q_y
   c. UB = min(UB, inv_cost(x*) + Σ Q_y)
   d. Generate optimality cuts: θ[y] ≥ intercept + gradient^T × x
   e. Check convergence: (UB - LB) / |UB| < tolerance
"""

# =============================================================================
# Constants
# =============================================================================

# Cost scaling factor for numerical stability in Benders cuts.
# theta[y] represents operational_cost / COST_SCALE.
# Cuts and objective account for this scaling, so final results are in actual dollars.
# With COST_SCALE = 1e6, theta is in millions of dollars, keeping LP coefficients
# within ~1e6 instead of ~1e12 (well within HiGHS tolerance).
const COST_SCALE = 1e6

# Maximum loss-of-load penalty ($/MW per timestep) used in Benders subproblems.
# The monolithic model uses Big-M penalties (e.g. $10M) which work fine for a single
# solve, but in Benders they create trillion-dollar subproblem costs that make cuts
# ineffective (intercepts overflow, gradients too steep → theta stays at 0).
# Cap at $10,000/MW-timestep: still ~60× the most expensive generator ($167/MWh diesel),
# so load shedding remains heavily penalized. At the optimal solution load shedding ≈ 0,
# so the capped and uncapped problems have the same optimal investment decisions.
# With temporal resolution 6h, diesel fuel_cost=$167/MWh is the most expensive generator.
# Cap at $250 (1.5× diesel) gives a max annual load-shed cost of ~$219M vs optimal ~$50M,
# a 4:1 ratio that Benders cuts can approximate effectively.
# Default fallback only — overridden by input.benders_lol_penalty_cap when available
const BENDERS_LOL_PENALTY_CAP_DEFAULT = 1000.0

# =============================================================================
# Types
# =============================================================================

"""Fixed capacity values passed from master to each subproblem."""
struct FixedCapacities
    # Existing generators: (gen_idx, bus) => Float64 effective capacity (MW)
    gen_capacity::Dict{Tuple{Int,Int}, Float64}
    # Existing batteries: (bat_idx, bus) => Float64 (MW or MWh)
    bat_power::Dict{Tuple{Int,Int}, Float64}
    bat_energy::Dict{Tuple{Int,Int}, Float64}
    # Technology investments: (tech_idx, bus) => Float64 cumulative capacity (MW)
    tech_capacity::Dict{Tuple{Int,Int}, Float64}
    # Battery technology investments: (bat_tech_idx, bus) => Float64
    bat_tech_power::Dict{Tuple{Int,Int}, Float64}
    bat_tech_energy::Dict{Tuple{Int,Int}, Float64}
    # Transmission: line_idx => Float64 total capacity (MW)
    line_capacity::Dict{Int, Float64}
    # RE ratios for this year (per system, from master target)
    re_ratios::Vector{Float64}
end

"""A single Benders optimality cut."""
struct BendersCut
    intercept::Float64
    # Gradients w.r.t. cumulative capacity parameters (aggregated across timesteps)
    # (tech, bus) => marginal value of capacity
    tech_gradients::Dict{Tuple{Int,Int}, Float64}
    bat_tech_pow_gradients::Dict{Tuple{Int,Int}, Float64}
    bat_tech_cap_gradients::Dict{Tuple{Int,Int}, Float64}
    # (gen, bus) => marginal value of life extension capacity
    gen_life_ext_gradients::Dict{Tuple{Int,Int}, Float64}
    bat_life_ext_gradients::Dict{Tuple{Int,Int}, Float64}
    # line_idx => marginal value of line capacity
    line_gradients::Dict{Int, Float64}
end

"""Subproblem data container for a single representative day."""
struct SubproblemData
    model::Model
    ps_vars::PowerSystemVariables
    tech_output::Any                    # tech dispatch variables (or nothing)
    bat_tech_discharge::Any
    bat_tech_charge::Any
    bat_tech_soc::Any
    # Named constraint references for dual extraction
    constraint_store::Dict{String, Any}
    # Metadata
    year_idx::Int
    day_idx::Int
    hours::Int
    start_hour::Int
    scaling_factor::Float64             # for cost annualization (tres * days_in_year / num_rep_days)
end

# =============================================================================
# RE Capacity–Target Linking (Benders master)
# =============================================================================

"""
    add_benders_re_capacity_linking!(model, vars, input, targets)

Link RE investment variables to RE penetration targets in the Benders master.

Without this, `re_penetration_ratio[y]` is a free variable that trivially
equals the target without requiring any RE investment.  This constraint
estimates annual RE energy potential from:
  - Existing RE generators (age-based retirement + degradation)
  - Technology investments (cumulative, with degradation)
and requires the total to cover `target[y] × annual_demand`.

The estimate uses average availability across all timesteps for each
(technology, bus) pair.
"""
function add_benders_re_capacity_linking!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput,
    targets::Dict{Tuple{Int,Int}, Float64}
)
    num_years = length(input.years)
    n_gen = length(input.generators)
    n_tech = length(input.technologies)
    n_buses = input.network.num_buses
    ypp = vars.years_per_inv_period

    # Precompute average availability per technology per bus
    avg_avail = Dict{Tuple{Int,Int}, Float64}()
    for tc in 1:n_tech
        tech = input.technologies[tc]
        if tech.type != "Renewable"
            continue
        end
        for b in 1:n_buses
            avail_hours = size(tech.availability, 1)
            if avail_hours > 0
                avg_avail[(tc, b)] = sum(tech.availability[:, b]) / avail_hours
            else
                avg_avail[(tc, b)] = 0.0
            end
        end
    end

    # Build system ranges (default: single system covering all buses)
    sys_ranges = input.system_node_ranges
    if isempty(sys_ranges)
        sys_ranges = [SystemNodeRange("global", 1, n_buses, input.initial_re_penetration)]
    end

    # For each year and each system, estimate RE energy and require >= target × demand
    for y_idx in 1:num_years
        growth_factor = (1.0 + input.demand_growth)^(y_idx - 1)
        timesteps_per_year = length(input.hours_per_year) > 0 ?
            input.hours_per_year[y_idx] ÷ input.temporal_resolution_hours : 8760
        year_start = (y_idx - 1) * timesteps_per_year + 1
        year_end = min(y_idx * timesteps_per_year, size(input.base_demand, 1))
        ts_per_year = Float64(timesteps_per_year)

        for (s_idx, sr) in enumerate(sys_ranges)
            target = get(targets, (s_idx, y_idx), 0.0)
            if target <= 0.0
                continue
            end

            sys_first = sr.first_bus
            sys_last = sr.first_bus + sr.num_buses - 1

            # Annual demand for this system's buses
            annual_demand = sum(input.base_demand[year_start:year_end, sys_first:sys_last]) * growth_factor

            # --- Existing RE generators (only system buses) ---
            existing_re_energy = 0.0
            for g in 1:n_gen
                gen = input.generators[g]
                if gen.type != "Renewable"
                    continue
                end
                for b in sys_first:sys_last
                    rated = gen.rated_power[b]
                    if rated <= 0
                        continue
                    end
                    lifetime = gen.life_time[b]
                    initial_age = gen.initial_age[b]
                    age = initial_age + (y_idx - 1)
                    if age >= lifetime
                        continue
                    end
                    deg = (1.0 - gen.degradation_rate[b])^age
                    avail_hours = size(gen.availability, 1)
                    avg_a = avail_hours > 0 ? sum(gen.availability[:, b]) / avail_hours : 0.3
                    existing_re_energy += rated * deg * avg_a * ts_per_year
                end
            end

            # --- Technology investment RE capacity (only system buses) ---
            tech_re_energy = AffExpr(0.0)
            for tc in 1:n_tech
                tech = input.technologies[tc]
                if tech.type != "Renewable"
                    continue
                end
                tech_lifetime = tech.life_time[1]
                tech_deg = tech.degradation_rate[1]
                for b in sys_first:sys_last
                    avg_a = get(avg_avail, (tc, b), 0.0)
                    if avg_a <= 0.0
                        continue
                    end
                    for y in 1:ypp:y_idx
                        inv_age = y_idx - y
                        if inv_age < tech_lifetime
                            deg = (1.0 - tech_deg) ^ inv_age
                            coeff = deg * avg_a * ts_per_year
                            add_to_expression!(tech_re_energy,
                                vars.tech_investment[y][tc][b], coeff)
                        end
                    end
                end
            end

            @constraint(model,
                existing_re_energy + tech_re_energy >= target * annual_demand,
                base_name = "re_cap_link_s$(s_idx)_y$(y_idx)")

            # NOTE: No hard upper bound on RE energy here.  The storage adequacy
            # constraint (add_benders_storage_adequacy!) handles excess RE by
            # requiring matching battery investment.  A hard upper bound would
            # prevent the master from investing in RE+battery combinations that
            # the subproblem can efficiently dispatch.
        end
    end
end


# =============================================================================
# Storage Adequacy (Benders master)
# =============================================================================

"""
    add_benders_storage_adequacy!(model, vars, input, targets)

Link battery investment to RE investment in the Benders master.

Standard Benders decomposition suffers from **dual degeneracy** when battery
capacity is zero: all three battery variable types (charge, discharge, SOC)
are simultaneously constrained to zero, so each individual constraint dual
is zero—even though the joint value of adding battery capacity is large.
This means the Benders cuts never signal the master to invest in batteries.

Fix: for each year and system, require battery energy capacity ≥ the
estimated daily excess RE energy (scaled by round-trip efficiency and the
max curtailment ratio).  The daily excess is computed from the availability
profiles' intra-day temporal mismatch—no hardcoded values.

Physics: solar generates in ~half the day but demand is spread over the
full day.  The excess energy above the intra-day average must be stored
(or curtailed).  This constraint ensures enough storage to absorb
(1 - max_curtailment_ratio) of that excess.
"""
function add_benders_storage_adequacy!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput,
    targets::Dict{Tuple{Int,Int}, Float64}
)
    n_tech = length(input.technologies)
    n_bat_tech = length(input.battery_technologies)
    if n_bat_tech == 0
        return  # nothing to constrain
    end
    n_buses = input.network.num_buses
    num_years = length(input.years)
    ypp = vars.years_per_inv_period
    tres = Float64(input.temporal_resolution_hours)
    timesteps_per_day = round(Int, 24.0 / tres)

    # ---- Compute temporal mismatch per RE technology per bus ----
    # For each 24h period in the availability profile, mismatch = the total
    # excess energy in timesteps where availability exceeds the daily average.
    # Units: MWh per MW of installed capacity per average day.
    mismatch = Dict{Tuple{Int,Int}, Float64}()
    for tc in 1:n_tech
        tech = input.technologies[tc]
        if tech.type != "Renewable"
            continue
        end
        for b in 1:n_buses
            avail_rows = size(tech.availability, 1)
            if avail_rows < timesteps_per_day
                continue
            end
            n_full_days = avail_rows ÷ timesteps_per_day
            total_excess = 0.0
            for d in 1:n_full_days
                day_start = (d - 1) * timesteps_per_day + 1
                day_avail = [tech.availability[day_start + ts - 1, b] for ts in 1:timesteps_per_day]
                day_mean = sum(day_avail) / timesteps_per_day
                for ts in 1:timesteps_per_day
                    excess = day_avail[ts] - day_mean
                    if excess > 0
                        total_excess += excess * tres  # MWh per MW
                    end
                end
            end
            mismatch[(tc, b)] = total_excess / n_full_days  # average daily excess
        end
    end

    # ---- Compute average round-trip efficiency from battery technologies ----
    avg_eff = 0.9  # safe default
    eff_sum = 0.0
    eff_count = 0
    for bt in 1:n_bat_tech
        btech = input.battery_technologies[bt]
        for b in 1:n_buses
            ch = btech.charge_efficiency[b]
            dis = btech.discharge_efficiency[b]
            if ch > 0 && dis > 0
                eff_sum += sqrt(ch * dis)
                eff_count += 1
            end
        end
    end
    if eff_count > 0
        avg_eff = eff_sum / eff_count
    end

    # ---- Build per-system, per-year storage adequacy constraints ----
    sys_ranges = input.system_node_ranges
    if isempty(sys_ranges)
        sys_ranges = [SystemNodeRange("global", 1, n_buses, input.initial_re_penetration)]
    end

    curt_margin = input.max_curtailment_ratio  # e.g. 0.05

    for y_idx in 1:num_years
        for (s_idx, sr) in enumerate(sys_ranges)
            target = get(targets, (s_idx, y_idx), 0.0)
            if target <= 0.0
                continue
            end

            sys_first = sr.first_bus
            sys_last = sr.first_bus + sr.num_buses - 1

            # LHS: total battery energy capacity (cumulative) in this system
            bat_energy_expr = AffExpr(0.0)
            for bt in 1:n_bat_tech
                btech = input.battery_technologies[bt]
                bt_lifetime = btech.life_time[1]
                for b in sys_first:sys_last
                    for y in 1:ypp:y_idx
                        inv_age = y_idx - y
                        if inv_age < bt_lifetime
                            add_to_expression!(bat_energy_expr,
                                vars.bat_tech_capacity_investment[y][bt][b], 1.0)
                        end
                    end
                end
            end

            # RHS: excess RE energy requiring storage (per average day)
            # excess = Σ tech_cumul_cap × mismatch_per_day
            excess_re_expr = AffExpr(0.0)
            for tc in 1:n_tech
                tech = input.technologies[tc]
                if tech.type != "Renewable"
                    continue
                end
                tech_lifetime = tech.life_time[1]
                tech_deg = tech.degradation_rate[1]
                for b in sys_first:sys_last
                    m = get(mismatch, (tc, b), 0.0)
                    if m <= 0.0
                        continue
                    end
                    for y in 1:ypp:y_idx
                        inv_age = y_idx - y
                        if inv_age < tech_lifetime
                            deg = (1.0 - tech_deg) ^ inv_age
                            add_to_expression!(excess_re_expr,
                                vars.tech_investment[y][tc][b], deg * m)
                        end
                    end
                end
            end

            # Constraint: bat_energy × efficiency ≥ (1 - curt_margin) × daily_excess
            # Battery cycles once per day; energy capacity must cover one day's excess.
            @constraint(model,
                bat_energy_expr * avg_eff >= (1.0 - curt_margin) * excess_re_expr,
                base_name = "storage_adequacy_s$(s_idx)_y$(y_idx)")
        end
    end
end


# =============================================================================
# Benders Master Problem (investment only)
# =============================================================================

"""
    create_benders_master(input)

Create investment-only master problem with recourse variables θ[y].
Reuses existing constraint functions but skips operational embedding.
"""
function create_benders_master(
    input::MasterProblemInput
)
    model = Model(create_optimizer(
        solver_name=input.solver_name,
        threads=input.threads,
        time_limit=min(input.time_limit, 600.0),  # master should be fast
        gap=input.gap,
        verbose=input.verbose
    ))

    # Presolve helps with initial model reduction
    if lowercase(input.solver_name) == "highs"
        set_attribute(model, "presolve", "on")
    end

    targets = calculate_target_ratios(input)
    vars = build_master_variables!(model, input)

    # Add all investment-only constraints (reuse existing functions)
    add_investment_constraints!(model, vars, input)
    add_budget_constraints!(model, vars, input)
    add_retirement_cascade_constraints!(model, vars, input)
    add_capacity_adequacy_constraints!(model, vars, input)
    add_transmission_symmetry_constraints!(model, vars, input)
    add_re_target_constraints!(model, vars, input, targets)
    add_re_increment_constraints!(model, vars, input)

    # =========================================================================
    # RE capacity–target linking constraints (Benders-specific)
    # =========================================================================
    # In the monolithic formulation, re_penetration_ratio is linked to actual
    # generation via add_annual_renewable_constraints!.  In Benders the
    # subproblems handle generation, but the master has NO link between
    # investment variables and re_penetration_ratio — meaning the RE target is
    # trivially satisfied by setting the free variable, without investing.
    #
    # Fix: for each year, estimate the annual renewable energy potential from
    # existing + invested RE capacity (capacity × avg availability × hours/year)
    # and require it to cover target × demand.  This is an approximation; the
    # subproblems refine it, but it forces the master to invest in enough RE
    # capacity to have any hope of meeting the target.
    # =========================================================================
    add_benders_re_capacity_linking!(model, vars, input, targets)

    # Storage adequacy: link battery investment to RE investment via temporal
    # mismatch.  Without this, Benders dual degeneracy at zero battery capacity
    # prevents any signal for battery investment from reaching the master.
    add_benders_storage_adequacy!(model, vars, input, targets)

    # NOTE: NO add_representative_days_validation! — this is the decomposed part

    # Add recourse variables θ[y] (one per year, approximates operational cost)
    # θ[y] is in SCALED units (actual_cost / COST_SCALE) to keep LP coefficients
    # in a numerically stable range. Cuts and objective use COST_SCALE accordingly.
    num_years = length(input.years)
    theta = Dict{Int, VariableRef}()
    for y_idx in 1:num_years
        theta[y_idx] = @variable(model,
            lower_bound = 0,
            base_name = "theta_y$(y_idx)")
    end

    # Objective: investment cost + discounted θ
    inv_cost_expr = build_investment_cost_expression(vars, input)

    # Per-year costs (life extension, decommissioning, slack penalties) + θ
    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_buses = input.network.num_buses
    total_cost = copy(inv_cost_expr)

    for y_idx in 1:num_years
        discount_factor = 1.0 / ((1.0 + input.discount_rate)^(y_idx - 1))
        annual_cost = AffExpr(0.0)

        # Life extension costs
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

        # Decommissioning costs (same as build_master_objective!)
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
                retirement_y = max(1, ceil(Int, lifetime - initial_age + 1))

                if y_idx == retirement_y
                    age = initial_age + y_idx - 1
                    deg = (1.0 - gen.degradation_rate[n]) ^ age
                    full_cap = rated_power * deg
                    add_to_expression!(annual_cost, full_cap * decomm_cost)
                    lev = vars.gen_life_extension[y_idx][g][n]
                    if lev !== nothing
                        add_to_expression!(annual_cost, lev, -decomm_cost)
                    end
                elseif y_idx > retirement_y
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

        add_to_expression!(total_cost, annual_cost, discount_factor)

        # Add discounted theta (operational cost approximation)
        # theta is in scaled units, so multiply by COST_SCALE to get actual $
        add_to_expression!(total_cost, theta[y_idx], discount_factor * COST_SCALE)

        # Slack penalties
        add_to_expression!(total_cost, vars.slack_re_target[y_idx], input.slack_penalty)
        add_to_expression!(total_cost, vars.slack_budget[y_idx], input.slack_penalty)
        for ni in 1:input.network.num_nodes
            add_to_expression!(total_cost, vars.slack_capacity[(y_idx, ni)], input.slack_penalty)
        end
    end

    @objective(model, Min, total_cost)

    return model, vars, targets, theta, inv_cost_expr
end

# =============================================================================
# Fixed Capacity Evaluation
# =============================================================================

"""
    evaluate_fixed_capacities(vars, input, year_idx)

Compute fixed capacity values from master solution for a given year.
Same logic as build_cumulative_capacity_expressions but with value() on variables.
"""
function evaluate_fixed_capacities(
    vars::MasterProblemVariables,
    input::MasterProblemInput,
    year_idx::Int
)::FixedCapacities
    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_tech = length(input.technologies)
    n_bat_tech = length(input.battery_technologies)
    n_buses = input.network.num_buses
    ypp = vars.years_per_inv_period

    # Existing generator capacity (with life extension)
    gen_cap = Dict{Tuple{Int,Int}, Float64}()
    for g in 1:n_gen
        gen = input.generators[g]
        for b in 1:n_buses
            cap = 0.0
            rated_power = gen.rated_power[b]
            lifetime = gen.life_time[b]
            initial_age = gen.initial_age[b]
            age_at_year = initial_age + (year_idx - 1)
            degradation_rate = gen.degradation_rate[b]

            if age_at_year < lifetime && rated_power > 0
                cap = rated_power * (1.0 - degradation_rate) ^ age_at_year
            elseif rated_power > 0
                life_ext_var = vars.gen_life_extension[year_idx][g][b]
                if life_ext_var !== nothing
                    cap = value(life_ext_var)
                end
            end
            if cap > 1e-8
                gen_cap[(g, b)] = cap
            end
        end
    end

    # Existing battery capacity (with life extension)
    bat_pow = Dict{Tuple{Int,Int}, Float64}()
    bat_en = Dict{Tuple{Int,Int}, Float64}()
    for bi in 1:n_bat
        bat = input.batteries[bi]
        for b in 1:n_buses
            pow = 0.0
            en = 0.0
            base_power = bat.max_discharge_power[b]
            base_cap = bat.capacity[b]
            lifetime = bat.life_time[b]
            initial_age = bat.initial_age[b]
            age_at_year = initial_age + (year_idx - 1)

            if age_at_year < lifetime
                pow = base_power > 0 ? base_power : 0.0
                en = base_cap > 0 ? base_cap : 0.0
            elseif base_power > 0
                life_ext_var = vars.bat_life_extension[year_idx][bi][b]
                if life_ext_var !== nothing
                    pow = value(life_ext_var)
                    en = base_cap > 0 ? pow * (base_cap / base_power) : 0.0
                end
            end
            if pow > 1e-8
                bat_pow[(bi, b)] = pow
            end
            if en > 1e-8
                bat_en[(bi, b)] = en
            end
        end
    end

    # Technology cumulative investment capacity
    tech_cap = Dict{Tuple{Int,Int}, Float64}()
    for t in 1:n_tech
        tech = input.technologies[t]
        for b in 1:n_buses
            cumul = 0.0
            tech_lifetime = tech.life_time[b]
            tech_deg = tech.degradation_rate[b]
            for y in 1:ypp:year_idx
                inv_age = year_idx - y
                if inv_age < tech_lifetime
                    deg = (1.0 - tech_deg) ^ inv_age
                    cumul += value(vars.tech_investment[y][t][b]) * deg
                end
            end
            if cumul > 1e-8
                tech_cap[(t, b)] = cumul
            end
        end
    end

    # Battery technology cumulative investment
    bt_pow = Dict{Tuple{Int,Int}, Float64}()
    bt_en = Dict{Tuple{Int,Int}, Float64}()
    for bt in 1:n_bat_tech
        btech = input.battery_technologies[bt]
        for b in 1:n_buses
            pow = 0.0
            en = 0.0
            bt_lifetime = btech.life_time[b]
            for y in 1:ypp:year_idx
                inv_age = year_idx - y
                if inv_age < bt_lifetime
                    pow += value(vars.bat_tech_power_investment[y][bt][b])
                    en += value(vars.bat_tech_capacity_investment[y][bt][b])
                end
            end
            if pow > 1e-8
                bt_pow[(bt, b)] = pow
            end
            if en > 1e-8
                bt_en[(bt, b)] = en
            end
        end
    end

    # Line capacity (base + cumulative investment)
    line_cap = Dict{Int, Float64}()
    # Line capacity is computed per subproblem since it depends on the network topology
    # (handled in create_benders_subproblem)

    # RE ratios from master — per system for per-system subproblem constraints
    n_sys = max(1, length(input.system_node_ranges))
    re_ratios = [value(vars.re_penetration_ratio[(s, year_idx)]) for s in 1:n_sys]

    return FixedCapacities(
        gen_cap, bat_pow, bat_en,
        tech_cap, bt_pow, bt_en,
        line_cap, re_ratios
    )
end

# =============================================================================
# Benders Subproblem
# =============================================================================

"""
    create_benders_subproblem(input, year_idx, day_idx, demand, start_hour, fixed_caps, vars)

Create and solve an operational dispatch subproblem for a single representative day
with FIXED capacities from the master solution.

Returns SubproblemData with solved model and constraint references for dual extraction.
"""
function create_benders_subproblem(
    input::MasterProblemInput,
    year_idx::Int,
    day_idx::Int,
    demand::Matrix{Float64},
    start_hour::Int,
    fixed_caps::FixedCapacities,
    master_vars::MasterProblemVariables
)::SubproblemData
    hours = size(demand, 1)
    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_tech = length(input.technologies)
    n_bat_tech = length(input.battery_technologies)
    n_buses = input.network.num_buses
    b2n = input.network.bus_to_node
    ypp = master_vars.years_per_inv_period

    constraint_store = Dict{String, Any}()

    # Create separate JuMP model for this subproblem
    # Use single thread and default scaling to avoid HiGHS OTHER_ERROR.
    # Multi-threaded HiGHS can have intermittent failures on certain LP structures.
    sp_model = Model(() -> begin
        opt = HiGHS.Optimizer()
        MOI.set(opt, MOI.RawOptimizerAttribute("threads"), 1)
        MOI.set(opt, MOI.RawOptimizerAttribute("time_limit"), min(input.time_limit, 300.0))
        MOI.set(opt, MOI.RawOptimizerAttribute("output_flag"), false)
        MOI.set(opt, MOI.RawOptimizerAttribute("presolve"), "on")
        return opt
    end)

    # Create PowerSystemVariables (reuse existing function)
    ps_vars = create_day_ps_vars!(sp_model, input, year_idx, day_idx, hours)

    # Apply demand growth
    growth_factor = (1.0 + input.demand_growth)^(year_idx - 1)

    # =========================================================================
    # Capacity overrides using FIXED values (Float64, not AffExpr)
    # =========================================================================
    total_cap_gen = Dict{Tuple{Int,Int}, Any}()
    for g in 1:n_gen
        for b in ps_vars.buses_of_gen[g]
            cap = get(fixed_caps.gen_capacity, (g, b), 0.0)
            total_cap_gen[(g, b)] = cap
            # Store constraint name pattern for dual extraction
            # (actual constraints created by add_generator_constraints!)
        end
    end

    total_cap_bat_power = Dict{Tuple{Int,Int}, Any}()
    total_cap_bat_energy = Dict{Tuple{Int,Int}, Any}()
    for bi in 1:n_bat
        for b in ps_vars.buses_of_bat[bi]
            total_cap_bat_power[(bi, b)] = get(fixed_caps.bat_power, (bi, b), 0.0)
            total_cap_bat_energy[(bi, b)] = get(fixed_caps.bat_energy, (bi, b), 0.0)
        end
    end

    # =========================================================================
    # Technology dispatch variables with FIXED capacity constraints
    # =========================================================================
    tech_output = nothing
    if n_tech > 0
        tech_output = @variable(sp_model,
            [t=1:n_tech, b=1:n_buses, ts=1:hours],
            lower_bound = 0,
            base_name = "tech_out_y$(year_idx)_d$(day_idx)")

        for t in 1:n_tech
            tech = input.technologies[t]
            is_renewable = tech.type == "Renewable"
            for b in 1:n_buses
                cumul_cap = get(fixed_caps.tech_capacity, (t, b), 0.0)
                for ts in 1:hours
                    actual_hour = start_hour + ts - 1
                    avail_hours = size(tech.availability, 1)
                    avail_hour = avail_hours > 0 ? mod1(actual_hour, avail_hours) : 1
                    avail = is_renewable ? tech.availability[avail_hour, b] : 1.0
                    rhs = cumul_cap * avail
                    cname = "tech_cap_t$(t)_b$(b)_ts$(ts)"
                    cref = @constraint(sp_model,
                        tech_output[t, b, ts] <= rhs,
                        base_name = cname)
                    constraint_store[cname] = (cref, avail, t, b, ts)
                end
            end
        end
    end

    # Battery technology dispatch with FIXED capacity
    bat_tech_discharge = nothing
    bat_tech_charge = nothing
    bat_tech_soc = nothing
    if n_bat_tech > 0
        bat_tech_discharge = @variable(sp_model,
            [bt=1:n_bat_tech, b=1:n_buses, ts=1:hours],
            lower_bound = 0, base_name = "bat_tech_dis_y$(year_idx)_d$(day_idx)")
        bat_tech_charge = @variable(sp_model,
            [bt=1:n_bat_tech, b=1:n_buses, ts=1:hours],
            lower_bound = 0, base_name = "bat_tech_ch_y$(year_idx)_d$(day_idx)")
        bat_tech_soc = @variable(sp_model,
            [bt=1:n_bat_tech, b=1:n_buses, ts=0:hours],
            lower_bound = 0, base_name = "bat_tech_soc_y$(year_idx)_d$(day_idx)")

        for bt in 1:n_bat_tech
            btech = input.battery_technologies[bt]
            for b in 1:n_buses
                pow_cap = get(fixed_caps.bat_tech_power, (bt, b), 0.0)
                en_cap = get(fixed_caps.bat_tech_energy, (bt, b), 0.0)

                for ts in 1:hours
                    cname_dis = "bt_dis_cap_bt$(bt)_b$(b)_ts$(ts)"
                    cref_dis = @constraint(sp_model, bat_tech_discharge[bt, b, ts] <= pow_cap,
                        base_name = cname_dis)
                    constraint_store[cname_dis] = (cref_dis, 1.0, bt, b, ts)

                    cname_ch = "bt_ch_cap_bt$(bt)_b$(b)_ts$(ts)"
                    cref_ch = @constraint(sp_model, bat_tech_charge[bt, b, ts] <= pow_cap,
                        base_name = cname_ch)
                    constraint_store[cname_ch] = (cref_ch, 1.0, bt, b, ts)
                end
                for ts in 0:hours
                    cname_soc = "bt_soc_cap_bt$(bt)_b$(b)_ts$(ts)"
                    cref_soc = @constraint(sp_model, bat_tech_soc[bt, b, ts] <= en_cap,
                        base_name = cname_soc)
                    constraint_store[cname_soc] = (cref_soc, 1.0, bt, b, ts)
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
                        @constraint(sp_model, bat_tech_soc[bt, b, ts] <= 0)
                    end
                    for ts in 1:hours
                        @constraint(sp_model, bat_tech_discharge[bt, b, ts] <= 0)
                        @constraint(sp_model, bat_tech_charge[bt, b, ts] <= 0)
                    end
                else
                    @constraint(sp_model, bat_tech_soc[bt, b, 0] == en_cap * initial_soc_frac)
                    for ts in 1:hours
                        @constraint(sp_model,
                            bat_tech_soc[bt, b, ts] == bat_tech_soc[bt, b, ts-1]
                                + ch_eff * bat_tech_charge[bt, b, ts]
                                - (1.0/dis_eff) * bat_tech_discharge[bt, b, ts])
                    end
                    @constraint(sp_model, bat_tech_soc[bt, b, hours] == en_cap * initial_soc_frac)
                end
            end
        end
    end

    # =========================================================================
    # Build day input NamedTuple (same as add_day_operational_constraints!)
    # =========================================================================
    n_nodes = input.network.num_nodes
    demand_slice = Matrix{Float64}(undef, hours, n_nodes)
    for t in 1:hours, n in 1:n_nodes
        demand_slice[t, n] = demand[t, n] * growth_factor
    end

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
                gen.risk_coefficient
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
        enable_angle_limits = false,
        re_penetration_target = 0.0,
        soc_end_tolerance = 0.0,
        min_cycling_ratio = 0.0,
        min_cycling_period_days = 1.0,
        pwl_loss_segments = input.transmission_loss_segments,
        gen_cost_curves = input.gen_cost_curves,
        bat_cost_curves = input.bat_cost_curves,
    )

    # =========================================================================
    # Delegate to power_system.jl constraints (with Float64 capacity_override)
    # =========================================================================
    add_generator_constraints!(sp_model, ps_vars, day_input;
        capacity_override=total_cap_gen)
    add_battery_constraints!(sp_model, ps_vars, day_input;
        capacity_override_power=total_cap_bat_power,
        capacity_override_energy=total_cap_bat_energy)
    add_reserve_constraints!(sp_model, ps_vars, day_input;
        capacity_override=total_cap_gen,
        demand_scale=1.0)
    add_inertia_constraints!(sp_model, ps_vars, day_input)

    # =========================================================================
    # Extra injections for power balance (tech + bat_tech output)
    # =========================================================================
    extra_fn = (bus, t) -> begin
        expr = AffExpr(0.0)
        if tech_output !== nothing
            for tc in 1:n_tech
                add_to_expression!(expr, tech_output[tc, bus, t])
            end
        end
        if bat_tech_discharge !== nothing
            for btc in 1:n_bat_tech
                add_to_expression!(expr, bat_tech_discharge[btc, bus, t])
                add_to_expression!(expr, -1.0, bat_tech_charge[btc, bus, t])
            end
        end
        if ps_vars.loss_of_load_sectoral !== nothing
            for (sector, lol_vars) in ps_vars.loss_of_load_sectoral
                add_to_expression!(expr, lol_vars[bus, t])
            end
        end
        return expr
    end

    # =========================================================================
    # Transmission / power balance
    # =========================================================================
    network_lines = input.network.transmission_lines
    n_lines = length(network_lines)
    if n_buses > 1 && n_lines > 0
        transmission = TransmissionDC(input.network)
        add_dc_constraints!(sp_model, transmission, ps_vars, day_input;
            extra_injections_fn=extra_fn)

        # Line capacity with FIXED cumulative investment
        line_cap_override = Dict{Int, Any}()
        for (l, (from, to)) in enumerate(transmission.lines)
            base_cap = transmission.line_capacities[l]
            inv_cap = 0.0
            for y in 1:ypp:year_idx
                if haskey(master_vars.transfer_investment[y], (from, to))
                    inv_cap += try
                        value(master_vars.transfer_investment[y][(from, to)])
                    catch
                        0.0  # Master not yet solved (e.g., warm-start phase)
                    end
                end
            end
            total = base_cap + inv_cap
            line_cap_override[l] = total
            # Store for dual extraction
            constraint_store["line_cap_$(l)"] = (from, to, total)
        end
        add_line_capacity_constraints!(sp_model, transmission, ps_vars, day_input;
            capacity_override=line_cap_override)
    else
        add_demand_constraints!(sp_model, ps_vars, day_input;
            extra_injections_fn=extra_fn)
    end

    # Curtailment
    add_curtailment_constraints!(sp_model, ps_vars, day_input;
        capacity_override=total_cap_gen)

    # =========================================================================
    # Sectoral Loss-of-Load Upper Bounds
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
                        @constraint(sp_model,
                            lol_vars[n, t] <= max(0.0, sec_demand_val))
                    end
                end
            end
        end
    end

    # =========================================================================
    # RE Penetration with FIXED ratio — PER SYSTEM
    # =========================================================================
    # Build per-system renewable generation and demand expressions.
    # When system_node_ranges is defined, each system gets its own RE constraint
    # so the optimizer can't concentrate all RE in one subsystem.
    n_sys = max(1, length(input.system_node_ranges))
    sys_ranges = input.system_node_ranges

    # Global totals (still needed for curtailment constraint)
    total_renewable = AffExpr(0.0)
    total_generation = AffExpr(0.0)

    # Per-system renewable expressions
    sys_renewable = [AffExpr(0.0) for _ in 1:n_sys]
    sys_fre_loss = [AffExpr(0.0) for _ in 1:n_sys]
    sys_demand_day = zeros(n_sys)

    # Helper: map bus → system index
    bus_to_sys = ones(Int, n_buses)  # default to system 1
    if length(sys_ranges) > 0
        for s_idx in 1:length(sys_ranges)
            sr = sys_ranges[s_idx]
            for b in sr.first_bus:(sr.first_bus + sr.num_buses - 1)
                if b <= n_buses
                    bus_to_sys[b] = s_idx
                end
            end
        end
    end

    for g in 1:n_gen
        gen = input.generators[g]
        is_renewable = gen.type == "Renewable"
        for n in ps_vars.buses_of_gen[g]
            s = bus_to_sys[n]
            for t in 1:hours
                add_to_expression!(total_generation, ps_vars.gen_output[g, n, t])
                if is_renewable
                    add_to_expression!(total_renewable, ps_vars.gen_output[g, n, t])
                    add_to_expression!(sys_renewable[s], ps_vars.gen_output[g, n, t])
                end
            end
        end
    end
    if tech_output !== nothing
        for tc in 1:n_tech
            tech = input.technologies[tc]
            is_renewable = tech.type == "Renewable"
            for b in 1:n_buses
                s = bus_to_sys[b]
                for t in 1:hours
                    add_to_expression!(total_generation, tech_output[tc, b, t])
                    if is_renewable
                        add_to_expression!(total_renewable, tech_output[tc, b, t])
                        add_to_expression!(sys_renewable[s], tech_output[tc, b, t])
                    end
                end
            end
        end
    end

    # FRE loss per system (node-level variables)
    n_nodes = input.network.num_nodes
    # Map nodes to systems via the bus_to_sys mapping (use first bus of each node)
    node_to_sys = zeros(Int, n_nodes)
    for b in 1:n_buses
        ni = input.network.bus_to_node[b]
        if node_to_sys[ni] == 0
            node_to_sys[ni] = bus_to_sys[b]
        end
    end
    for ni in 1:n_nodes
        s = node_to_sys[ni]
        for t in 1:hours
            add_to_expression!(sys_fre_loss[s], ps_vars.fre_penetration_loss[ni, t])
        end
    end

    # Per-system demand on this representative day
    # demand is already sliced to [hours × nodes] for this representative day
    total_demand_day = sum(demand) * growth_factor
    for n in 1:n_buses
        s = bus_to_sys[n]
        parent_node = input.network.bus_to_node[n]
        bus_fraction = input.network.buses[n].demand_fraction
        for t in 1:hours
            sys_demand_day[s] += demand[t, parent_node] * bus_fraction * growth_factor
        end
    end

    # Per-system RE constraints (hard equality on gross RE dispatch)
    # Matches monolithic model: re_penetration_ratio == target.
    # Hard upper bound on GROSS RE prevents over-dispatching beyond target.
    for s_idx in 1:n_sys
        target_ratio = length(fixed_caps.re_ratios) >= s_idx ? fixed_caps.re_ratios[s_idx] : fixed_caps.re_ratios[1]

        @constraint(sp_model,
            sys_renewable[s_idx] + sys_fre_loss[s_idx] >= target_ratio * sys_demand_day[s_idx],
            base_name = "re_min_day_s$(s_idx)_y$(year_idx)_d$(day_idx)")

        @constraint(sp_model,
            sys_renewable[s_idx] + sys_fre_loss[s_idx] <= target_ratio * sys_demand_day[s_idx],
            base_name = "re_max_day_s$(s_idx)_y$(year_idx)_d$(day_idx)")
    end

    # Max curtailment — includes BOTH generator and technology RE curtailment.
    # Technology curtailment = fixed_cap × avail - tech_output (capacity not dispatched).
    # Including it here is critical: when the RE upper bound (not the capacity
    # constraint) limits tech dispatch, the capacity dual is 0 and the master
    # gets no signal about overcapacity.  By including technology curtailment,
    # the dual of this constraint captures the cost of over-investment and
    # feeds it into the Benders cuts via extract_subproblem_duals.
    total_curtailment = @expression(sp_model,
        sum(ps_vars.curtailment[n, t] for n in 1:n_nodes, t in 1:hours))

    # Add technology RE curtailment: available capacity - dispatched output
    # tech_avail_sum tracks Σ(fixed_cap × avail) per (tech, bus) for dual extraction
    tech_avail_sum = Dict{Tuple{Int,Int}, Float64}()
    if tech_output !== nothing
        for tc in 1:n_tech
            tech = input.technologies[tc]
            if tech.type != "Renewable"
                continue
            end
            for b in 1:n_buses
                cumul_cap = get(fixed_caps.tech_capacity, (tc, b), 0.0)
                if cumul_cap <= 0
                    continue
                end
                avail_total = 0.0
                for ts in 1:hours
                    actual_hour = start_hour + ts - 1
                    avail_hours = size(tech.availability, 1)
                    avail_hour = avail_hours > 0 ? mod1(actual_hour, avail_hours) : 1
                    avail = tech.availability[avail_hour, b]
                    # tech curtailment = cap × avail - tech_output
                    add_to_expression!(total_curtailment, cumul_cap * avail)
                    add_to_expression!(total_curtailment, -1.0, tech_output[tc, b, ts])
                    avail_total += avail
                end
                tech_avail_sum[(tc, b)] = avail_total
            end
        end
    end

    curt_excess_slack = @variable(sp_model,
        lower_bound = 0,
        base_name = "curt_excess_y$(year_idx)_d$(day_idx)")
    curt_cref = nothing
    if input.max_curtailment_ratio < 1.0
        curt_cref = @constraint(sp_model,
            total_curtailment <= input.max_curtailment_ratio * total_renewable + curt_excess_slack,
            base_name = "max_curt_y$(year_idx)_d$(day_idx)")
        # Store for dual extraction — needed for Benders cut gradient
        constraint_store["max_curt"] = (curt_cref, tech_avail_sum)
    end

    # =========================================================================
    # Objective: operational cost (includes annualization scaling factor)
    # =========================================================================
    # Compute scaling factor first (needed for penalty cap adjustment)
    tres = max(1.0, input.temporal_resolution_hours)
    days_in_year = length(input.hours_per_year) > 0 ? input.hours_per_year[1] / 24.0 : 365.0
    scaling_factor = tres * days_in_year / input.representative_days_per_year

    day_cost = calculate_day_operational_cost(ps_vars, input, year_idx, hours;
        tech_output=tech_output)

    # Cap load-shedding penalties for Benders numerical stability.
    # calculate_day_operational_cost already includes scaling_factor internally,
    # so our adjustment must also include it.
    lol_penalty_cap = input.benders_lol_penalty_cap
    if lol_penalty_cap > 0 && input.loss_of_load_penalty > lol_penalty_cap
        lol_diff = lol_penalty_cap - input.loss_of_load_penalty  # negative
        for t in 1:hours
            for n in 1:n_nodes
                add_to_expression!(day_cost, ps_vars.load_shed[n, t], lol_diff * scaling_factor)
            end
        end
        # Also cap sectoral loss-of-load penalties (which use criticality multipliers)
        if ps_vars.loss_of_load_sectoral !== nothing && !isempty(ps_vars.loss_of_load_sectoral)
            for (sector, lol_vars) in ps_vars.loss_of_load_sectoral
                criticality = get(input.sectoral_criticality, sector, 1.0)
                orig_penalty = input.loss_of_load_penalty * criticality
                capped_penalty = lol_penalty_cap * criticality
                sec_diff = capped_penalty - orig_penalty  # negative
                for t in 1:hours
                    for n in 1:n_buses
                        add_to_expression!(day_cost, lol_vars[n, t], sec_diff * scaling_factor)
                    end
                end
            end
        end
    end

    # Curtailment penalties — feeds back to Benders master via cuts.
    # The max_curtailment_ratio soft constraint (above) tracks total curtailment
    # including technology investments.  Only EXCESS curtailment (above the
    # threshold) is penalized — a flat per-MWh curtailment cost would make
    # batteries artificially attractive by inflating the gradient signal.
    if input.curtailment_excess_penalty > 0
        add_to_expression!(day_cost, curt_excess_slack,
            input.curtailment_excess_penalty * scaling_factor)
    end
    # NOTE: re_excess_penalty no longer used — RE upper bound is now hard
    # (matching monolithic model's equality constraint behavior).

    @objective(sp_model, Min, day_cost)

    return SubproblemData(
        sp_model, ps_vars,
        tech_output, bat_tech_discharge, bat_tech_charge, bat_tech_soc,
        constraint_store,
        year_idx, day_idx, hours, start_hour, scaling_factor
    )
end

# =============================================================================
# Dual Extraction and Cut Generation
# =============================================================================

"""
    extract_subproblem_duals(sp, input)

Extract aggregated dual values from a solved subproblem for Benders cut generation.
Returns duals aggregated by (tech/bat_tech, bus) across timesteps.
"""
function extract_subproblem_duals(
    sp::SubproblemData,
    input::MasterProblemInput
)
    n_tech = length(input.technologies)
    n_bat_tech = length(input.battery_technologies)
    n_buses = input.network.num_buses

    # Technology capacity duals: aggregate across timesteps
    # Constraint: tech_output[t,b,ts] <= cap * avail(ts)
    # dual(cref) = ∂Q/∂RHS where RHS = cap * avail
    # We need ∂Q/∂cap = Σ_ts dual(cref_ts) × avail(ts)  (chain rule)
    tech_duals = Dict{Tuple{Int,Int}, Float64}()
    for t in 1:n_tech
        for b in 1:n_buses
            total_dual = 0.0
            for ts in 1:sp.hours
                cname = "tech_cap_t$(t)_b$(b)_ts$(ts)"
                if haskey(sp.constraint_store, cname)
                    cref, avail, _, _, _ = sp.constraint_store[cname]
                    d = dual(cref)
                    total_dual += d * avail
                end
            end
            if abs(total_dual) > 1e-10
                tech_duals[(t, b)] = total_dual
            end
        end
    end

    # Battery tech power duals (discharge)
    bt_pow_duals = Dict{Tuple{Int,Int}, Float64}()
    for bt in 1:n_bat_tech
        for b in 1:n_buses
            total_dual = 0.0
            for ts in 1:sp.hours
                cname_dis = "bt_dis_cap_bt$(bt)_b$(b)_ts$(ts)"
                cname_ch = "bt_ch_cap_bt$(bt)_b$(b)_ts$(ts)"
                if haskey(sp.constraint_store, cname_dis)
                    cref_dis, _, _, _, _ = sp.constraint_store[cname_dis]
                    total_dual += dual(cref_dis)
                end
                if haskey(sp.constraint_store, cname_ch)
                    cref_ch, _, _, _, _ = sp.constraint_store[cname_ch]
                    total_dual += dual(cref_ch)
                end
            end
            if abs(total_dual) > 1e-10
                bt_pow_duals[(bt, b)] = total_dual
            end
        end
    end

    # Battery tech energy duals (SOC)
    bt_cap_duals = Dict{Tuple{Int,Int}, Float64}()
    for bt in 1:n_bat_tech
        for b in 1:n_buses
            total_dual = 0.0
            for ts in 0:sp.hours
                cname_soc = "bt_soc_cap_bt$(bt)_b$(b)_ts$(ts)"
                if haskey(sp.constraint_store, cname_soc)
                    cref_soc, _, _, _, _ = sp.constraint_store[cname_soc]
                    total_dual += dual(cref_soc)
                end
            end
            if abs(total_dual) > 1e-10
                bt_cap_duals[(bt, b)] = total_dual
            end
        end
    end

    # Curtailment constraint dual contribution to technology gradient.
    # The curtailment constraint includes technology curtailment:
    #   Σ(curt) + Σ(cap×avail - tech_output) ≤ ratio×renewable + slack
    # cap (master variable) appears on the LHS with coefficient Σ_ts(avail).
    # The gradient ∂Q/∂cap from this constraint = -dual(curt_cref) × Σ_ts(avail).
    # (negative because cap increases the LHS, tightening a ≤ constraint;
    #  JuMP dual for ≤ in minimization is ≤ 0, so -dual ≥ 0 = cost increase)
    # This term is CRITICAL: when the RE upper bound (not the capacity
    # constraint) limits tech dispatch, the capacity dual is 0 but the
    # curtailment dual captures the marginal cost of over-investment.
    if haskey(sp.constraint_store, "max_curt")
        curt_data = sp.constraint_store["max_curt"]
        curt_cref = curt_data[1]
        tech_avail_sum = curt_data[2]
        curt_dual = dual(curt_cref)  # ≤ 0 for ≤ constraint in minimization
        if abs(curt_dual) > 1e-10
            for ((tc, b), avail_sum) in tech_avail_sum
                # -curt_dual × avail_sum is the positive gradient (cost increases with capacity)
                curt_contribution = -curt_dual * avail_sum
                tech_duals[(tc, b)] = get(tech_duals, (tc, b), 0.0) + curt_contribution
            end
        end
    end

    return tech_duals, bt_pow_duals, bt_cap_duals
end

"""
    build_optimality_cut(sp_cost, tech_duals, bt_pow_duals, bt_cap_duals,
                         fixed_caps, input, year_idx, ypp)

Construct a Benders optimality cut from subproblem duals.

Cut form: θ[y] ≥ Q(x*) + Σ π_i × (x_i - x_i*)
       =  (Q(x*) - Σ π_i × x_i*) + Σ π_i × x_i
       =  intercept + gradient^T × x
"""
function build_optimality_cut(
    sp_cost::Float64,
    tech_duals::Dict{Tuple{Int,Int}, Float64},
    bt_pow_duals::Dict{Tuple{Int,Int}, Float64},
    bt_cap_duals::Dict{Tuple{Int,Int}, Float64},
    fixed_caps::FixedCapacities,
    input::MasterProblemInput,
    year_idx::Int,
    ypp::Int
)::BendersCut
    # The dual π[(t,b)] is w.r.t. cumulative capacity at (t,b) for this year
    # Chain rule: d(cumul_cap[y][t][b]) / d(inv[y'][t][b]) = deg_factor if age < lifetime
    # So the gradient w.r.t. investment variable inv[y'][t][b] is:
    #   grad = π[(t,b)] * (1 - deg)^(year_idx - y')  for each prior period y'

    # For the cut intercept, we need: Q(x*) - Σ π_i × x_i*
    # where x_i* is the cumulative capacity (the parameter that was fixed)
    intercept = sp_cost
    for ((t, b), pi) in tech_duals
        cap_star = get(fixed_caps.tech_capacity, (t, b), 0.0)
        intercept -= pi * cap_star
    end
    for ((bt, b), pi) in bt_pow_duals
        cap_star = get(fixed_caps.bat_tech_power, (bt, b), 0.0)
        intercept -= pi * cap_star
    end
    for ((bt, b), pi) in bt_cap_duals
        cap_star = get(fixed_caps.bat_tech_energy, (bt, b), 0.0)
        intercept -= pi * cap_star
    end

    # Technology gradients: π[(t,b)] maps to investment variables via cumulative sum
    tech_grads = Dict{Tuple{Int,Int}, Float64}()
    for ((t, b), pi) in tech_duals
        tech = input.technologies[t]
        tech_lifetime = tech.life_time[b]
        tech_deg = tech.degradation_rate[b]
        for y in 1:ypp:year_idx
            inv_age = year_idx - y
            if inv_age < tech_lifetime
                deg = (1.0 - tech_deg) ^ inv_age
                key = (t, b)
                # The gradient is w.r.t. the investment variable at year y
                # We store it keyed by (t, b) for this specific y
                # But we need to distinguish by investment year for the cut
                grad_key = (t * 1000 + y, b)  # encode year into key
                tech_grads[grad_key] = get(tech_grads, grad_key, 0.0) + pi * deg
            end
        end
    end

    # Battery tech power gradients
    bt_pow_grads = Dict{Tuple{Int,Int}, Float64}()
    for ((bt, b), pi) in bt_pow_duals
        btech = input.battery_technologies[bt]
        bt_lifetime = btech.life_time[b]
        for y in 1:ypp:year_idx
            inv_age = year_idx - y
            if inv_age < bt_lifetime
                grad_key = (bt * 1000 + y, b)
                bt_pow_grads[grad_key] = get(bt_pow_grads, grad_key, 0.0) + pi
            end
        end
    end

    # Battery tech energy gradients
    bt_cap_grads = Dict{Tuple{Int,Int}, Float64}()
    for ((bt, b), pi) in bt_cap_duals
        btech = input.battery_technologies[bt]
        bt_lifetime = btech.life_time[b]
        for y in 1:ypp:year_idx
            inv_age = year_idx - y
            if inv_age < bt_lifetime
                grad_key = (bt * 1000 + y, b)
                bt_cap_grads[grad_key] = get(bt_cap_grads, grad_key, 0.0) + pi
            end
        end
    end

    # No normalization here — scaling is handled at the master level via COST_SCALE

    return BendersCut(
        intercept,
        tech_grads,
        bt_pow_grads,
        bt_cap_grads,
        Dict{Tuple{Int,Int}, Float64}(),  # gen_life_ext (not yet extracted)
        Dict{Tuple{Int,Int}, Float64}(),  # bat_life_ext (not yet extracted)
        Dict{Int, Float64}()              # line gradients (not yet extracted)
    )
end

"""
    add_optimality_cut!(model, theta, cut, year_idx, vars, input, iteration)

Add a single Benders optimality cut to the master problem.
θ[y] ≥ intercept + Σ gradient × investment_variable
"""
function add_optimality_cut!(
    model::Model,
    theta::Dict{Int, VariableRef},
    cut::BendersCut,
    year_idx::Int,
    vars::MasterProblemVariables,
    input::MasterProblemInput,
    iteration::Int
)
    n_buses = input.network.num_buses
    ypp = vars.years_per_inv_period

    # Divide all cut coefficients by COST_SCALE since theta is in scaled units
    inv_scale = 1.0 / COST_SCALE

    rhs = AffExpr(cut.intercept * inv_scale)

    # Technology investment gradients
    for ((encoded_key, b), grad) in cut.tech_gradients
        t = encoded_key ÷ 1000
        y = encoded_key % 1000
        if haskey(vars.tech_investment, y) && haskey(vars.tech_investment[y], t)
            add_to_expression!(rhs, vars.tech_investment[y][t][b], grad * inv_scale)
        end
    end

    # Battery tech power investment gradients
    for ((encoded_key, b), grad) in cut.bat_tech_pow_gradients
        bt = encoded_key ÷ 1000
        y = encoded_key % 1000
        if haskey(vars.bat_tech_power_investment, y) && haskey(vars.bat_tech_power_investment[y], bt)
            add_to_expression!(rhs, vars.bat_tech_power_investment[y][bt][b], grad * inv_scale)
        end
    end

    # Battery tech capacity investment gradients
    for ((encoded_key, b), grad) in cut.bat_tech_cap_gradients
        bt = encoded_key ÷ 1000
        y = encoded_key % 1000
        if haskey(vars.bat_tech_capacity_investment, y) && haskey(vars.bat_tech_capacity_investment[y], bt)
            add_to_expression!(rhs, vars.bat_tech_capacity_investment[y][bt][b], grad * inv_scale)
        end
    end

    @constraint(model,
        theta[year_idx] >= rhs,
        base_name = "benders_cut_y$(year_idx)_iter$(iteration)")
end

# =============================================================================
# Main Benders Decomposition Loop
# =============================================================================

"""
    run_benders_decomposition(input; kwargs...)

Run Benders decomposition to solve the master problem.
Separates investment (master) from operational (subproblems) decisions.

# Arguments
- `input::MasterProblemInput`: Problem specification
- `max_iterations::Int=50`: Maximum iterations
- `tolerance::Float64=1e-4`: Relative gap tolerance for convergence
- `use_representative_days::Bool=true`: Use representative days (vs TSAM)
- `verbose_benders::Bool=true`: Log iteration progress

# Returns
- `BendersResult`: Solution with convergence history
"""
function run_benders_decomposition(
    input::MasterProblemInput;
    max_iterations::Int = 50,
    tolerance::Float64 = 1e-4,
    use_representative_days::Bool = true,
    verbose_benders::Bool = true
)::BendersResult
    start_time = time()
    num_years = length(input.years)
    timesteps_per_day = 24 ÷ input.temporal_resolution_hours

    # =========================================================================
    # Step 0: Create Benders master
    # =========================================================================
    @info "Benders: Creating investment master problem..."
    master_model, vars, targets, theta, inv_cost_expr = create_benders_master(input)

    # =========================================================================
    # Step 1: Pre-select representative days for each year
    # =========================================================================
    rep_days_by_year = Dict{Int, Vector{Int}}()
    for y_idx in 1:num_years
        timesteps_per_year = input.hours_per_year[y_idx] ÷ input.temporal_resolution_hours
        rep_days = select_representative_days(
            input.base_demand,
            y_idx,
            input.representative_days_per_year,
            input.min_day_separation,
            timesteps_per_day,
            timesteps_per_year
        )
        rep_days_by_year[y_idx] = rep_days
    end

    # =========================================================================
    # Step 1.5: Warm-start — Compute minimum operational costs as theta lower bounds
    # =========================================================================
    # With no theta bounds, the master invests minimally (theta=0 means "operational
    # cost is free"). Subproblems then have massive load shedding → cuts have
    # trillion-dollar intercepts that never bind. Fix: compute Q(∞) for each year
    # (dispatch with unlimited capacity → no load shedding → minimum possible
    # operational cost). This is a valid theta lower bound since Q(x) >= Q(∞)
    # (more capacity can only help). Also generate cuts from a high-investment point.
    @info "Benders: Computing warm-start theta bounds..."
    n_gen_ws = length(input.generators)
    n_bat_ws = length(input.batteries)
    n_tech_ws = length(input.technologies)
    n_bat_tech_ws = length(input.battery_technologies)
    n_buses_ws = input.network.num_buses

    for y_idx in 1:num_years
        # Create FixedCapacities with very large capacity (no load shedding)
        warm_gen_cap = Dict{Tuple{Int,Int}, Float64}()
        for g in 1:n_gen_ws
            gen = input.generators[g]
            for b in 1:n_buses_ws
                rated = gen.rated_power[b]
                if rated > 0
                    warm_gen_cap[(g, b)] = rated  # full original capacity
                end
            end
        end
        warm_bat_pow = Dict{Tuple{Int,Int}, Float64}()
        warm_bat_en = Dict{Tuple{Int,Int}, Float64}()
        for bi in 1:n_bat_ws
            bat = input.batteries[bi]
            for b in 1:n_buses_ws
                pow = bat.max_discharge_power[b]
                cap = bat.capacity[b]
                if pow > 0
                    warm_bat_pow[(bi, b)] = pow
                    warm_bat_en[(bi, b)] = cap
                end
            end
        end
        # Large technology capacity (1000 MW each) — eliminates capacity constraints
        warm_tech_cap = Dict{Tuple{Int,Int}, Float64}()
        for t in 1:n_tech_ws
            for b in 1:n_buses_ws
                warm_tech_cap[(t, b)] = 1000.0
            end
        end
        warm_bt_pow = Dict{Tuple{Int,Int}, Float64}()
        warm_bt_en = Dict{Tuple{Int,Int}, Float64}()
        for bt in 1:n_bat_tech_ws
            for b in 1:n_buses_ws
                warm_bt_pow[(bt, b)] = 1000.0
                warm_bt_en[(bt, b)] = 5000.0
            end
        end

        n_sys_ws = max(1, length(input.system_node_ranges))
        warm_re_ratios = [haskey(targets, (s, y_idx)) ? targets[(s, y_idx)] : 0.0 for s in 1:n_sys_ws]
        warm_caps = FixedCapacities(
            warm_gen_cap, warm_bat_pow, warm_bat_en,
            warm_tech_cap, warm_bt_pow, warm_bt_en,
            Dict{Int, Float64}(),  # line capacity (handled in subproblem)
            warm_re_ratios
        )

        year_min_cost = 0.0
        year_tech_duals = Dict{Tuple{Int,Int}, Float64}()
        year_bt_pow_duals = Dict{Tuple{Int,Int}, Float64}()
        year_bt_cap_duals = Dict{Tuple{Int,Int}, Float64}()

        for (day_idx, start_hour) in enumerate(rep_days_by_year[y_idx])
            end_hour = min(start_hour + timesteps_per_day - 1, size(input.base_demand, 1))
            hours = end_hour - start_hour + 1
            if hours < 1 continue end
            day_demand = input.base_demand[start_hour:end_hour, :]

            sp = create_benders_subproblem(
                input, y_idx, day_idx, day_demand, start_hour, warm_caps, vars
            )
            optimize!(sp.model)
            sp_status = termination_status(sp.model)
            if sp_status == MOI.OPTIMAL || sp_status == MOI.LOCALLY_SOLVED
                year_min_cost += objective_value(sp.model)
                if has_duals(sp.model)
                    td, bpd, bcd = extract_subproblem_duals(sp, input)
                    for (k, v) in td
                        year_tech_duals[k] = get(year_tech_duals, k, 0.0) + v
                    end
                    for (k, v) in bpd
                        year_bt_pow_duals[k] = get(year_bt_pow_duals, k, 0.0) + v
                    end
                    for (k, v) in bcd
                        year_bt_cap_duals[k] = get(year_bt_cap_duals, k, 0.0) + v
                    end
                end
            end
        end

        # NOTE: We do NOT set theta lower bounds from Q(1000MW) here.
        # With curtailment_excess_penalty active, Q(1000MW) >> Q(optimal) because
        # 1000 MW/bus generates far more RE than demand, incurring huge curtailment
        # penalties. That makes Q(1000MW) an upper bound on Q, not a lower bound.
        # The theta variable already has lower_bound=0 from its declaration.

        # Add warm-start cut from high-investment point
        if !isempty(year_tech_duals) || !isempty(year_bt_pow_duals) || !isempty(year_bt_cap_duals)
            cut = build_optimality_cut(
                year_min_cost,
                year_tech_duals, year_bt_pow_duals, year_bt_cap_duals,
                warm_caps, input, y_idx, vars.years_per_inv_period
            )
            add_optimality_cut!(master_model, theta, cut, y_idx, vars, input, 0)
        end

        # Second warm-start: moderate battery capacity to reveal battery value.
        # The first warm-start uses oversized batteries (1000 MW) → constraints
        # never bind → battery duals are 0 → master gets no battery signal.
        # Here we use RE capacity ≈ demand/avg_avail and battery capacity ≈
        # peak_demand × 0.3, so battery constraints bind during peak solar
        # hours, producing non-zero duals that seed battery investment.
        if n_bat_tech_ws > 0
            peak_demand = maximum(sum(input.base_demand, dims=2))
            target_y = haskey(targets, (1, y_idx)) ? targets[(1, y_idx)] : 0.5

            mod_tech_cap = Dict{Tuple{Int,Int}, Float64}()
            for t in 1:n_tech_ws
                tech_ws = input.technologies[t]
                if tech_ws.type == "Renewable"
                    # Count buses with non-zero availability to distribute capacity
                    avail_hrs = size(tech_ws.availability, 1)
                    n_re_buses = 0
                    for b in 1:n_buses_ws
                        avg_a = avail_hrs > 0 ? sum(tech_ws.availability[:, b]) / avail_hrs : 0.0
                        if avg_a > 0.01
                            n_re_buses += 1
                        end
                    end
                    n_re_buses = max(n_re_buses, 1)
                    for b in 1:n_buses_ws
                        avg_a = avail_hrs > 0 ? sum(tech_ws.availability[:, b]) / avail_hrs : 0.3
                        # Distribute total system RE need across buses with availability
                        mod_tech_cap[(t, b)] = avg_a > 0 ? target_y * peak_demand / (avg_a * n_re_buses) : 0.0
                    end
                else
                    for b in 1:n_buses_ws
                        mod_tech_cap[(t, b)] = 0.0
                    end
                end
            end
            # Moderate battery: 30% of peak demand power, 4h duration
            mod_bt_pow = Dict{Tuple{Int,Int}, Float64}()
            mod_bt_en = Dict{Tuple{Int,Int}, Float64}()
            for bt in 1:n_bat_tech_ws
                for b in 1:n_buses_ws
                    mod_bt_pow[(bt, b)] = peak_demand * 0.3
                    mod_bt_en[(bt, b)] = peak_demand * 0.3 * 4.0
                end
            end
            mod_caps = FixedCapacities(
                warm_gen_cap, warm_bat_pow, warm_bat_en,
                mod_tech_cap, mod_bt_pow, mod_bt_en,
                Dict{Int, Float64}(), warm_re_ratios
            )

            mod_cost = 0.0
            mod_td = Dict{Tuple{Int,Int}, Float64}()
            mod_bpd = Dict{Tuple{Int,Int}, Float64}()
            mod_bcd = Dict{Tuple{Int,Int}, Float64}()

            for (day_idx2, start_hour2) in enumerate(rep_days_by_year[y_idx])
                end_hour2 = min(start_hour2 + timesteps_per_day - 1, size(input.base_demand, 1))
                hours2 = end_hour2 - start_hour2 + 1
                if hours2 < 1 continue end
                day_demand2 = input.base_demand[start_hour2:end_hour2, :]
                sp2 = create_benders_subproblem(
                    input, y_idx, day_idx2, day_demand2, start_hour2, mod_caps, vars
                )
                optimize!(sp2.model)
                sp2_status = termination_status(sp2.model)
                if sp2_status == MOI.OPTIMAL || sp2_status == MOI.LOCALLY_SOLVED
                    mod_cost += objective_value(sp2.model)
                    if has_duals(sp2.model)
                        td2, bpd2, bcd2 = extract_subproblem_duals(sp2, input)
                        for (k, v) in td2; mod_td[k] = get(mod_td, k, 0.0) + v; end
                        for (k, v) in bpd2; mod_bpd[k] = get(mod_bpd, k, 0.0) + v; end
                        for (k, v) in bcd2; mod_bcd[k] = get(mod_bcd, k, 0.0) + v; end
                    end
                end
            end

            if !isempty(mod_td) || !isempty(mod_bpd) || !isempty(mod_bcd)
                mod_cut = build_optimality_cut(
                    mod_cost, mod_td, mod_bpd, mod_bcd,
                    mod_caps, input, y_idx, vars.years_per_inv_period
                )
                add_optimality_cut!(master_model, theta, mod_cut, y_idx, vars, input, 0)
            end
        end

        if verbose_benders && (y_idx <= 3 || y_idx == num_years)
            @info "  Warm-start y$(y_idx): warm_cost = $(round(year_min_cost, sigdigits=4)) (cut added)"
        end
    end
    @info "Benders: Warm-start complete. Cuts added from high-investment points."

    # =========================================================================
    # Step 2: Benders iteration loop
    # =========================================================================
    lb_history = Float64[]
    ub_history = Float64[]
    best_ub = Inf
    converged = false
    # Best-primal tracking: save year_ops from the iteration that achieved best_ub.
    # Used at max-iterations to avoid using a potentially worse final-solve result.
    best_year_ops = zeros(num_years)

    # Create optimizer factory for resetting between iterations
    master_optimizer = create_optimizer(
        solver_name=input.solver_name,
        threads=input.threads,
        time_limit=min(input.time_limit, 600.0),
        gap=input.gap,
        verbose=input.verbose
    )

    for iter in 1:max_iterations
        # ----- Solve master -----
        # Reset optimizer state before each solve to avoid HiGHS internal state
        # issues when constraints are added incrementally after a previous solve.
        # The LP is re-sent from JuMP's cache, ensuring a clean solve.
        set_optimizer(master_model, master_optimizer)
        optimize!(master_model)
        master_status = termination_status(master_model)
        if master_status != MOI.OPTIMAL && master_status != MOI.LOCALLY_SOLVED
            @warn "Benders: Master solve failed at iteration $(iter): $(master_status)"
            try
                write_to_file(master_model, "/tmp/benders_master_failed_iter$(iter).lp")
                @info "  Debug LP written to /tmp/benders_master_failed_iter$(iter).lp"
            catch; end
            break
        end

        lb = objective_value(master_model)
        push!(lb_history, lb)

        # ----- Evaluate investment cost at current solution -----
        inv_cost_val = value(inv_cost_expr)

        if verbose_benders
            # Log theta values for diagnostics
            theta_sum_actual = sum(
                value(theta[y]) * COST_SCALE
                for y in 1:num_years
            )
            @info "Benders iteration $(iter): LB = $(round(lb, digits=2)), " *
                  "inv_cost = $(round(inv_cost_val, digits=0)), " *
                  "Σθ(actual\$) = $(round(theta_sum_actual, digits=0))"
        end

        # ----- Solve subproblems for each year -----
        total_operational_cost = 0.0
        year_operational_costs = zeros(num_years)
        year_cuts = Dict{Int, BendersCut}()
        all_subproblems_solved = true

        for y_idx in 1:num_years
            # Evaluate fixed capacities from master solution
            fixed_caps = evaluate_fixed_capacities(vars, input, y_idx)

            rep_days = rep_days_by_year[y_idx]
            if isempty(rep_days)
                continue
            end

            year_cost = 0.0
            year_tech_duals = Dict{Tuple{Int,Int}, Float64}()
            year_bt_pow_duals = Dict{Tuple{Int,Int}, Float64}()
            year_bt_cap_duals = Dict{Tuple{Int,Int}, Float64}()

            for (day_idx, start_hour) in enumerate(rep_days)
                end_hour = min(start_hour + timesteps_per_day - 1, size(input.base_demand, 1))
                hours = end_hour - start_hour + 1
                if hours < 1
                    continue
                end

                day_demand = input.base_demand[start_hour:end_hour, :]

                # Create and solve subproblem
                sp = create_benders_subproblem(
                    input, y_idx, day_idx, day_demand, start_hour, fixed_caps, vars
                )
                optimize!(sp.model)

                sp_status = termination_status(sp.model)
                if sp_status != MOI.OPTIMAL && sp_status != MOI.LOCALLY_SOLVED
                    # Retry with fresh optimizer: serial dual simplex, no scaling
                    set_optimizer(sp.model, HiGHS.Optimizer)
                    set_attribute(sp.model, "output_flag", false)
                    set_attribute(sp.model, "threads", 1)
                    set_attribute(sp.model, "simplex_strategy", 1)  # serial dual simplex
                    set_attribute(sp.model, "simplex_scale_strategy", 0)  # auto scaling
                    set_attribute(sp.model, "presolve", "on")
                    optimize!(sp.model)
                    sp_status = termination_status(sp.model)
                    if sp_status != MOI.OPTIMAL && sp_status != MOI.LOCALLY_SOLVED
                        @warn "Benders: Subproblem (y=$(y_idx), d=$(day_idx)) failed after retry: $(sp_status)"
                        all_subproblems_solved = false
                        # Save first failing LP for diagnosis
                        if iter <= 5
                            try
                                write_to_file(sp.model, "/tmp/benders_sp_failed_y$(y_idx)_d$(day_idx)_iter$(iter).lp")
                            catch; end
                        end
                        continue
                    end
                end

                sp_cost = objective_value(sp.model)
                year_cost += sp_cost

                # Extract duals
                if has_duals(sp.model)
                    td, bpd, bcd = extract_subproblem_duals(sp, input)
                    for (k, v) in td
                        year_tech_duals[k] = get(year_tech_duals, k, 0.0) + v
                    end
                    for (k, v) in bpd
                        year_bt_pow_duals[k] = get(year_bt_pow_duals, k, 0.0) + v
                    end
                    for (k, v) in bcd
                        year_bt_cap_duals[k] = get(year_bt_cap_duals, k, 0.0) + v
                    end
                end
            end

            discount_factor = 1.0 / ((1.0 + input.discount_rate)^(y_idx - 1))
            year_operational_costs[y_idx] = year_cost
            total_operational_cost += discount_factor * year_cost

            # Build aggregated cut for this year
            if !isempty(year_tech_duals) || !isempty(year_bt_pow_duals) || !isempty(year_bt_cap_duals)
                cut = build_optimality_cut(
                    year_cost,
                    year_tech_duals, year_bt_pow_duals, year_bt_cap_duals,
                    fixed_caps, input, y_idx, vars.years_per_inv_period
                )
                year_cuts[y_idx] = cut
            end
        end

        # ----- Compute upper bound -----
        # UB = first-stage cost (investment + life ext + decomm + slack) + actual operational cost
        # The master objective includes theta[y] * COST_SCALE * discount(y), so subtract that
        theta_contribution = sum(
            value(theta[y]) * COST_SCALE * (1.0 / ((1.0 + input.discount_rate)^(y - 1)))
            for y in 1:num_years
        )
        master_first_stage = value(objective_function(master_model)) - theta_contribution
        ub = master_first_stage + total_operational_cost
        # Only update best_ub when ALL subproblems solved successfully;
        # otherwise total_operational_cost understates the true cost
        if all_subproblems_solved && ub < best_ub
            best_ub = ub
            best_year_ops = copy(year_operational_costs)
        end
        push!(ub_history, best_ub)

        # ----- Check convergence -----
        gap = abs(best_ub - lb) / max(abs(best_ub), 1e-10)
        if verbose_benders
            @info "  UB = $(round(best_ub, digits=2)), gap = $(round(gap*100, digits=4))%, " *
                  "first_stage = $(round(master_first_stage, digits=0)), " *
                  "total_ops = $(round(total_operational_cost, digits=0))"
            # Log per-year breakdown for first 3 iterations
            if iter <= 3
                for y in 1:min(5, num_years)
                    theta_val = value(theta[y]) * COST_SCALE
                    disc = 1.0 / ((1.0 + input.discount_rate)^(y - 1))
                    @info "    y$(y): θ=$(round(theta_val, sigdigits=4)), " *
                          "actual_Q=$(round(year_operational_costs[y], sigdigits=4)), " *
                          "disc=$(round(disc, sigdigits=4))"
                end
            end
        end

        if gap < tolerance
            @info "Benders: Converged in $(iter) iterations (gap = $(round(gap*100, digits=4))%)"
            converged = true

            # Extract final solution
            solution = extract_master_solution_from_benders(
                master_model, vars, input, year_operational_costs
            )

            return BendersResult(
                solution, best_ub, iter, gap,
                lb_history, ub_history,
                time() - start_time
            )
        end

        # ----- Add cuts -----
        cuts_added = 0
        for (y_idx, cut) in sort(collect(year_cuts), by=first)
            add_optimality_cut!(master_model, theta, cut, y_idx, vars, input, iter)
            cuts_added += 1

            if verbose_benders && iter <= 3
                max_tech = isempty(cut.tech_gradients) ? 0.0 : maximum(abs, values(cut.tech_gradients))
                max_bt = isempty(cut.bat_tech_pow_gradients) ? 0.0 : maximum(abs, values(cut.bat_tech_pow_gradients))
                @info "    Cut y$(y_idx): intercept=$(round(cut.intercept, sigdigits=4)), " *
                      "max_tech_grad=$(round(max_tech, sigdigits=4)), max_bt_grad=$(round(max_bt, sigdigits=4)), " *
                      "n_tech=$(length(cut.tech_gradients)), n_bt=$(length(cut.bat_tech_pow_gradients))"
            end
        end

        if verbose_benders
            @info "  Added $(cuts_added) cuts"
        end
    end

    # Reached max iterations without convergence
    final_gap = abs(best_ub - lb_history[end]) / max(abs(best_ub), 1e-10)
    @warn "Benders: Max iterations ($(max_iterations)) reached. " *
          "Final gap = $(round(final_gap * 100, digits=4))%"

    # Solve master one final time so variable values are available for extraction.
    # (Adding cuts in the last iteration invalidates the cached solution.)
    set_optimizer(master_model, master_optimizer)
    optimize!(master_model)

    # Use the best-primal year_ops (from the iteration that achieved best_ub).
    # This avoids using a final-solve that may differ from the best-found solution.
    solution = extract_master_solution_from_benders(master_model, vars, input, best_year_ops)

    return BendersResult(
        solution, best_ub, max_iterations, final_gap,
        lb_history, ub_history,
        time() - start_time
    )
end

# =============================================================================
# Solution Extraction
# =============================================================================

"""
    extract_master_solution_from_benders(model, vars, input, year_operational_costs)

Extract a MasterProblemResult from the Benders master solution.
Reuses the logic from extract_master_solution but provides operational costs
from subproblem solves instead of embedded variables.
"""
function extract_master_solution_from_benders(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput,
    year_operational_costs::Vector{Float64}
)::MasterProblemResult
    # Use existing extraction (it handles investment vars, cumulative caps, etc.)
    result = extract_master_solution(model, vars, input)

    # Override operational costs with values from subproblem solves
    return MasterProblemResult(
        result.status,
        result.objective,
        result.solve_time,
        result.tech_investment,
        result.bat_tech_power_investment,
        result.bat_tech_capacity_investment,
        result.transfer_investment,
        result.gen_life_extension,
        result.bat_life_extension,
        result.total_investment_by_year,
        year_operational_costs,  # Override with actual subproblem costs
        result.re_penetration_by_year,
        result.re_penetration_by_system,
        result.cumulative_gen_capacity,
        result.cumulative_bat_capacity,
        result.cumulative_bat_power,
        result.cumulative_tech_capacity,
        result.cumulative_bat_tech_power,
        result.cumulative_bat_tech_capacity,
        result.reservoir_investment
    )
end
