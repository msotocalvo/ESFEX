"""
power_system.jl - PowerSystem optimization model

Implements the operational dispatch and unit commitment optimization
using JuMP. This is the core optimization model that handles:
- Generator dispatch and commitment
- Battery storage operation
- Transmission constraints (via TransmissionDC)
- Reserve requirements
- Renewable penetration targets
"""

# Note: JuMP, HiGHS, etc. are imported from the parent module (ESFEX.jl)
using JuMP: @variable, @constraint, @objective, @expression, Model
using JuMP: value, dual, has_values, has_duals, AffExpr, VariableRef
using JuMP: objective_value, termination_status, solve_time, optimizer_with_attributes
using JuMP: add_to_expression!, constraint_object

# =============================================================================
# Temporal Upscaling Utilities
# =============================================================================

"""
    compute_upscaling_indices(hours, points_per_day)

Compute upscaled time indices for reduced-resolution variables.
Returns (indices, time_map) where:
- indices: Vector of hour indices where upscaled variables are defined
- time_map: Dict mapping each hour to its closest upscaled index
"""
function compute_upscaling_indices(hours::Int, points_per_day::Int)
    if points_per_day <= 0 || points_per_day >= 24
        # No upscaling - use all hours
        indices = collect(0:hours)
        time_map = Dict(h => h + 1 for h in 0:hours)
        return indices, time_map
    end

    # Calculate indices at regular intervals within each day
    interval = 24 ÷ points_per_day
    indices = Int[]

    num_days = (hours + 23) ÷ 24
    for day in 0:(num_days - 1)
        for point in 0:(points_per_day - 1)
            hour = day * 24 + point * interval
            if hour <= hours
                push!(indices, hour)
            end
        end
    end

    # Ensure final hour is included
    if hours ∉ indices
        push!(indices, hours)
    end

    # Create mapping from each hour to closest upscaled index
    time_map = Dict{Int, Int}()
    for h in 0:hours
        closest_idx = argmin(abs.(indices .- h))
        time_map[h] = closest_idx
    end

    return indices, time_map
end

"""
    create_power_system(input::PowerSystemInput)

Create and return a JuMP model with variables for the power system optimization.

Returns a tuple (model, vars) where:
- model: JuMP Model ready for optimization
- vars: PowerSystemVariables containing all decision variables
"""
function create_power_system(input::PowerSystemInput)

    t_total = time()
    t0 = time()

    # Create model with configured optimizer
    optimizer = create_optimizer(
        solver_name=input.solver_name,
        threads=input.threads,
        time_limit=input.time_limit,
        gap=input.gap,
        verbose=input.verbose,
        solver_options=input.solver_options
    )
    model = Model(optimizer)
    t_optimizer = time() - t0

    # Build variables
    t0 = time()
    vars = build_variables!(model, input)
    t_variables = time() - t0

    # Build objective
    t0 = time()
    build_objective!(model, vars, input)
    t_objective = time() - t0

    # Build core constraints
    t0 = time()
    add_generator_constraints!(model, vars, input)
    t_gen_con = time() - t0

    t0 = time()
    add_battery_constraints!(model, vars, input)
    t_bat_con = time() - t0

    t0 = time()
    add_reservoir_constraints!(model, vars, input)
    t_reservoir = time() - t0

    t0 = time()
    add_reserve_constraints!(model, vars, input)
    t_reserve = time() - t0

    # EV fleet constraints (SOC dynamics, charge/V2G limits, mutual exclusivity)
    t0 = time()
    add_ev_constraints!(model, vars, input)
    t_ev = time() - t0

    # Add inertia constraints (if configured)
    t0 = time()
    add_inertia_constraints!(model, vars, input)
    t_inertia = time() - t0

    # Add curtailment definition (available_renewable - used_renewable)
    t0 = time()
    add_curtailment_constraints!(model, vars, input)
    t_curtailment = time() - t0

    # Add policy constraints
    t0 = time()
    add_renewable_constraint!(model, vars, input)
    add_co2_emissions_definition!(model, vars, input)  # Define CO2_emissions variable values
    add_co2_constraint!(model, vars, input)
    t_policy = time() - t0

    # Add power flow constraints based on selected mode
    # NOTE: For multi-node, KCL handles power balance
    # (including transmission flows, curtailment, and reserves)
    t0 = time()
    pf_mode = input.power_flow_mode
    is_acopf = startswith(pf_mode, "acopf_")

    t_transmission_build = 0.0
    if input.network.num_buses > 1
        if is_acopf
            # ACOPF formulations: SOC, QC, SDP, Polar NLP, Rectangular NLP
            # Losses are inherent in the AC formulation (no PWL needed).
            # Converter constraints are added inside setup_acopf!.
            acopf_vars = setup_acopf!(model, vars, input)
            vars.acopf_vars = acopf_vars
        else
            # DCOPF path (default): "dcopf" or "dcopf_ac_verify"
            t_tb = time()
            transmission = TransmissionDC(input.network)
            # Loss model: pwl_loss_segments > 0 → PWL, -1 → legacy linear, 0 → lossless
            if input.pwl_loss_segments > 0
                pwl = compute_pwl_loss_segments(transmission, input.pwl_loss_segments, Float64(input.network.base_impedance))
                transmission = TransmissionDC(
                    transmission.num_buses, transmission.lines,
                    transmission.line_reactances, transmission.line_capacities,
                    transmission.incidence_matrix, transmission.max_angle_diff_rad,
                    transmission.slack_bus, transmission.line_losses, pwl
                )
            elseif input.pwl_loss_segments == 0
                # Lossless mode: zero out line losses
                transmission = TransmissionDC(
                    transmission.num_buses, transmission.lines,
                    transmission.line_reactances, transmission.line_capacities,
                    transmission.incidence_matrix, transmission.max_angle_diff_rad,
                    transmission.slack_bus, zeros(length(transmission.lines)), nothing
                )
            end
            t_transmission_build = time() - t_tb
            # Add converter variables/constraints BEFORE DC constraints
            # so KCL can reference converter flow variables
            add_converter_constraints!(model, vars, input)
            add_dc_constraints!(model, transmission, vars, input)
            add_line_capacity_constraints!(model, transmission, vars, input)
        end
    else
        # For single-bus, add simple power balance
        add_converter_constraints!(model, vars, input)
        add_demand_constraints!(model, vars, input)
    end
    t_power_flow = time() - t0

    # Add sectoral demand constraints (B8: sum sectoral LOL == total LOL, criticality ordering)
    t0 = time()
    add_sectoral_demand_constraints!(model, vars, input)
    t_sectoral = time() - t0

    # Per-node aggregate investment constraint.
    # invest_max[b] is replicated (same for all buses at a node), but total
    # investment across buses at a node must not exceed the node budget.
    t0 = time()
    if input.mode == "development"
        n_bus = input.network.num_buses
        b2n = input.network.bus_to_node
        node_to_buses_inv = Dict{Int, Vector{Int}}()
        for b in 1:n_bus
            push!(get!(node_to_buses_inv, b2n[b], Int[]), b)
        end
        gen_buses_set = [Set(vars.buses_of_gen[g]) for g in 1:length(input.generators)]
        for g in 1:length(input.generators)
            gen = input.generators[g]
            for (ni, buses_at_node) in node_to_buses_inv
                length(buses_at_node) <= 1 && continue
                max_inv = gen.invest_max[buses_at_node[1]]
                max_inv > 0 || continue
                active_buses = [b for b in buses_at_node if b in gen_buses_set[g]]
                isempty(active_buses) && continue
                @constraint(model,
                    sum(vars.gen_investment[g, b] for b in active_buses) <= max_inv,
                    base_name = "invest_node_gen_g$(g)_n$(ni)")
            end
        end
        bat_buses_set = [Set(vars.buses_of_bat[bi]) for bi in 1:length(input.batteries)]
        for bi in 1:length(input.batteries)
            bat = input.batteries[bi]
            for (ni, buses_at_node) in node_to_buses_inv
                length(buses_at_node) <= 1 && continue
                max_inv_pow = bat.invest_max_power[buses_at_node[1]]
                max_inv_cap = bat.invest_max_capacity[buses_at_node[1]]
                active_buses_bat = [b for b in buses_at_node if b in bat_buses_set[bi]]
                if max_inv_pow > 0 && !isempty(active_buses_bat)
                    @constraint(model,
                        sum(vars.bat_investment_power[bi, b] for b in active_buses_bat) <= max_inv_pow,
                        base_name = "invest_node_bat_pow_bi$(bi)_n$(ni)")
                end
                if max_inv_cap > 0 && !isempty(active_buses_bat)
                    @constraint(model,
                        sum(vars.bat_investment_capacity[bi, b] for b in active_buses_bat) <= max_inv_cap,
                        base_name = "invest_node_bat_cap_bi$(bi)_n$(ni)")
                end
            end
        end
    end
    t_invest = time() - t0

    # Add node-level investment limits (B14)
    t0 = time()
    add_node_investment_limits!(model, vars, input)
    t_node_limits = time() - t0

    # Add max annual system cost constraint (B15)
    add_max_annual_system_cost!(model, vars, input)

    # Add N-1 security constraints
    t0 = time()
    if input.n1_scopf_enabled
        add_scopf_constraints!(model, vars, input)
    else
        add_n1_security_constraints!(model, vars, input)
    end
    t_n1 = time() - t0

    t_build_total = time() - t_total

    # Build-timing / size diagnostics at @debug level (quiet by default;
    # enable with JULIA_DEBUG=ESFEX).
    n_vars = num_variables(model)
    n_cons = num_constraints(model; count_variable_in_set_constraints=false)
    @debug "PowerSystem build complete" n_buses=input.network.num_buses hours=input.temporal.hours n_variables=n_vars n_constraints=n_cons t_build=round(t_build_total, digits=2) t_variables=round(t_variables, digits=2) t_objective=round(t_objective, digits=2) t_gen_constraints=round(t_gen_con, digits=2) t_power_flow=round(t_power_flow, digits=2) t_n1=round(t_n1, digits=2)

    return model, vars
end

"""
    build_variables!(model, input::PowerSystemInput) -> PowerSystemVariables

Create all decision variables for the power system model.
"""
function build_variables!(model, input::PowerSystemInput)
    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_bus = input.network.num_buses
    b2n = input.network.bus_to_node
    n_node = input.network.num_nodes
    hours = input.temporal.hours
    is_uc = input.mode == "unit_commitment"
    is_dev = input.mode == "development"

    # =========================================================================
    # Pre-compute sparse lookup maps: which (gen, bus) and (bat, bus) pairs
    # have capacity (existing, investable, or delayed retirement).
    # =========================================================================
    pending_gen = get(input.pending_retirements, "gen", Dict{Int, Dict{Int, Float64}}())
    pending_bat_ret = get(input.pending_retirements, "bat", Dict{Int, Dict{Int, Float64}}())

    buses_of_gen = [Int[] for _ in 1:n_gen]
    gens_at_bus = [Int[] for _ in 1:n_bus]
    for g in 1:n_gen
        gen = input.generators[g]
        for b in 1:n_bus
            rated = gen.rated_power[b]
            has_cap = rated > 0
            has_invest = is_dev && length(gen.invest_max) >= b && gen.invest_max[b] > 0
            has_delay = haskey(pending_gen, g) && haskey(pending_gen[g], b) && pending_gen[g][b] > 0
            if has_cap || has_invest || has_delay
                push!(buses_of_gen[g], b)
                push!(gens_at_bus[b], g)
            end
        end
    end

    buses_of_bat = [Int[] for _ in 1:n_bat]
    bats_at_bus = [Int[] for _ in 1:n_bus]
    for bi in 1:n_bat
        bat = input.batteries[bi]
        for b in 1:n_bus
            has_cap = bat.max_charge_power[b] > 0 ||
                      bat.max_discharge_power[b] > 0 ||
                      bat.capacity[b] > 0
            has_invest = is_dev && length(bat.invest_max_power) >= b &&
                         (bat.invest_max_power[b] > 0 || bat.invest_max_capacity[b] > 0)
            has_delay = haskey(pending_bat_ret, bi) && haskey(pending_bat_ret[bi], b) && pending_bat_ret[bi][b] > 0
            if has_cap || has_invest || has_delay
                push!(buses_of_bat[bi], b)
                push!(bats_at_bus[b], bi)
            end
        end
    end

    # Generator output — SparseAxisArray (only active gen-bus pairs)
    gen_output = @variable(model, [g=1:n_gen, b=buses_of_gen[g], t=1:hours], lower_bound=0, base_name="gen")

    # Curtailment per node (node × hour) - spilled renewable energy
    @variable(model, curtailment[1:n_node, 1:hours] >= 0)

    # FRE penetration loss slack (node × hour) - for RE penetration constraint
    @variable(model, fre_penetration_loss[1:n_node, 1:hours] >= 0)


    # Generator status and startup variables — SparseAxisArray
    # Matches Python legacy (power_system.py lines 1184-1190)
    gen_startup = nothing
    gen_shutdown = nothing

    if is_uc
        # Unit commitment mode: gen_status is Binary, gen_startup is Continuous [0,1]
        gen_status = @variable(model, [g=1:n_gen, b=buses_of_gen[g], t=1:hours], Bin, base_name="gen_status")
        gen_startup = @variable(model, [g=1:n_gen, b=buses_of_gen[g], t=1:hours], lower_bound=0, upper_bound=1, base_name="gen_startup")
        # gen_shutdown stays nothing (matches Python which has no shutdown variable)
    else
        # Economic dispatch / development mode: gen_status is implicitly 1 for
        # every (g,b,t). We do NOT create the variable — that would inject
        # millions of trivial fixed-bound variables that survive presolve and
        # bloat the LP. Downstream code must check `is_uc` before referencing
        # gen_status, or use `nothing` as a sentinel.
        gen_status = nothing
    end

    # CO2 emissions (node × hour)
    @variable(model, co2_emissions[1:n_node, 1:hours] >= 0)

    # Battery variables — SparseAxisArray (only active bat-bus pairs)
    bat_charge = @variable(model, [bi=1:n_bat, b=buses_of_bat[bi], t=1:hours], lower_bound=0, base_name="bat_charge")
    bat_discharge = @variable(model, [bi=1:n_bat, b=buses_of_bat[bi], t=1:hours], lower_bound=0, base_name="bat_discharge")
    # bat_soc has hours+1 elements: index 1 = initial state, indices 2:(hours+1) = state after each hour
    bat_soc = @variable(model, [bi=1:n_bat, b=buses_of_bat[bi], t=1:(hours+1)], lower_bound=0, base_name="bat_soc")
    # Battery charge status for mutex constraint — SparseAxisArray
    bat_charge_status = @variable(model, [bi=1:n_bat, b=buses_of_bat[bi], t=1:hours], lower_bound=0, upper_bound=1, base_name="bat_cs")

    # SOC violation slack variable — SparseAxisArray
    soc_violation = @variable(model, [bi=1:n_bat, b=buses_of_bat[bi], t=1:hours], lower_bound=0, base_name="soc_viol")

    # Battery spillage variables — SparseAxisArray for batteries that allow spillage
    bat_spillage = nothing
    has_any_spillage = n_bat > 0 && any(bat.spillage for bat in input.batteries)
    if has_any_spillage
        # Create spillage variables only for batteries that allow it, at active buses
        bat_spillage_var = Array{Union{VariableRef, Nothing}, 3}(nothing, n_bat, n_bus, hours)
        for bi in 1:n_bat
            if input.batteries[bi].spillage
                for b in buses_of_bat[bi], t in 1:hours
                    bat_spillage_var[bi, b, t] = @variable(model, lower_bound=0,
                        base_name="bat_spillage_$(bi)_$(b)_$(t)")
                end
            end
        end
        bat_spillage = bat_spillage_var
    end

    # Transmission
    power_flow = Dict{Tuple{Int,Int}, Vector{VariableRef}}()
    # Note: power flow variables are created per line in TransmissionDC

    # Voltage angles (bus × hour), bounded to [-π, π] rad
    @variable(model, -π <= voltage_angle[1:n_bus, 1:hours] <= π)

    # Transfer margin violation (from × to × hour)
    # Slack variable for transmission capacity violations with penalty.
    # Only create when DC-OPF is NOT active — when DC-OPF handles line flows,
    # these unconstrained slack variables are always zero (pure model bloat).
    transfer_margin = Dict{Tuple{Int,Int}, Vector{VariableRef}}()
    if isempty(input.network.transmission_lines)
        for i in 1:n_bus
            for j in 1:n_bus
                if i != j && get(input.network.connections, (i, j), 0) > 0
                    transfer_margin[(i, j)] = @variable(model, [1:hours], lower_bound=0,
                        base_name="transfer_margin_$(i)_$(j)")
                end
            end
        end
    end

    # Reserve variables (node × hour) — aggregated at geographic node level
    @variable(model, reserve_static[1:n_node, 1:hours] >= 0)
    @variable(model, reserve_dynamic[1:n_node, 1:hours] >= 0)
    @variable(model, reserve_static_loss[1:n_node, 1:hours] >= 0)
    @variable(model, reserve_dynamic_loss[1:n_node, 1:hours] >= 0)

    # Load shedding (bus × hour).
    #
    # NOTE: load_shed is per-BUS, not per-node.  A per-node load_shed
    # combined with the bus-level KCL `+ load_shed_n * bus_df_b` acts
    # as a free slack at every load bus: the per-bus dual (LMP) at any
    # load bus collapses to 0 because the LP can absorb 1 MW of demand
    # there by adding ~1/bus_df units to the node variable, distributing
    # the cost across all sibling buses simultaneously.  With LMP = 0
    # at load buses, generators with any positive marginal cost cannot
    # profitably dispatch even when shed at the node level costs VOLL.
    # Declaring shed per-bus gives each load bus its own price signal
    # so generation→demand routing is incentivised.
    @variable(model, load_shed[1:n_bus, 1:hours] >= 0)

    # Sectoral load shedding (sector -> bus × hour)
    # Created for each sector in sectoral_demand if available
    loss_of_load_sectoral = nothing
    flexible_demand_curtailed = nothing
    if !isempty(input.sectoral_demand)
        loss_of_load_sectoral = Dict{String, Matrix{VariableRef}}()
        # Only create flex_curt variables when the benefit ratio > 0 (i.e., flexible sectors exist).
        # Without this guard, flex_curt would be unbounded (only lower_bound=0, no upper bound in
        # the bounds section, and not appearing in any constraint), causing the LP to be UNBOUNDED
        # since the objective subtracts price * ratio * flex_curt.
        if input.flexible_demand_benefit_ratio > 0
            flexible_demand_curtailed = Dict{String, Matrix{VariableRef}}()
        end
        for sector in keys(input.sectoral_demand)
            loss_of_load_sectoral[sector] = @variable(model, [1:n_node, 1:hours],
                lower_bound=0, base_name="lol_$(sector)")
            if input.flexible_demand_benefit_ratio > 0
                flexible_demand_curtailed[sector] = @variable(model, [1:n_node, 1:hours],
                    lower_bound=0, base_name="flex_curt_$(sector)")
            end
        end
    end

    # Demand shifting variables (P3: sparse t-to-t_dest pairs with delay tolerance)
    # Matches Python legacy flexible_demand_shifted[sector][node][t][t_dest]
    demand_shift = nothing
    if !isempty(input.sectoral_demand) && !isempty(input.sectoral_criticality)
        demand_shift = Dict{String, Dict{Tuple{Int,Int,Int}, VariableRef}}()
        for (sector, crit) in input.sectoral_criticality
            if haskey(input.sectoral_demand, sector) && crit < 1.0  # Only flexible sectors
                delay = get(input.sectoral_delay_tolerance, sector, 24)
                sector_vars = Dict{Tuple{Int,Int,Int}, VariableRef}()
                for b in 1:n_node, t in 1:hours
                    t_min = max(1, t - delay)
                    t_max = min(hours, t + delay)
                    for t_dest in t_min:t_max
                        if t_dest != t
                            sector_vars[(b, t, t_dest)] = @variable(model,
                                lower_bound=0,
                                base_name="shift_$(sector)_$(b)_$(t)_$(t_dest)")
                        end
                    end
                end
                demand_shift[sector] = sector_vars
            end
        end
    end

    # Investment variables (development mode only)
    gen_investment = nothing
    bat_investment_power = nothing
    bat_investment_capacity = nothing
    transfer_investment = nothing

    if is_dev
        gen_investment = @variable(model, [g=1:n_gen, b=buses_of_gen[g]], lower_bound=0, base_name="gen_inv")
        bat_investment_power = @variable(model, [bi=1:n_bat, b=buses_of_bat[bi]], lower_bound=0, base_name="bat_inv_p")
        bat_investment_capacity = @variable(model, [bi=1:n_bat, b=buses_of_bat[bi]], lower_bound=0, base_name="bat_inv_c")
        transfer_investment = Dict{Tuple{Int,Int}, VariableRef}()
    end

    # EV variables (optional, only if ev_config is provided)
    # Node-level (not bus-level) to reduce variable count for large networks.
    # In power balance, scaled by bus demand_fraction (same as load_shed, reserves).
    ev_charging = nothing
    ev_v2g = nothing
    ev_soc = nothing
    ev_loss = nothing
    ev_charge_status = nothing
    if input.ev_config !== nothing
        @variable(model, ev_charging_var[1:n_node, 1:hours] >= 0)
        @variable(model, ev_v2g_var[1:n_node, 1:hours] >= 0)
        @variable(model, ev_soc_var[1:n_node, 1:(hours+1)] >= 0)
        @variable(model, ev_loss_var[1:n_node, 1:hours] >= 0)
        # Charge/discharge mutual exclusivity status [0,1] continuous relaxation
        @variable(model, 0 <= ev_charge_status_var[1:n_node, 1:hours] <= 1)
        ev_charging = ev_charging_var
        ev_v2g = ev_v2g_var
        ev_soc = ev_soc_var
        ev_loss = ev_loss_var
        ev_charge_status = ev_charge_status_var
    end

    # Electrolyzer power variable (optional)
    electrolyzer_power = nothing
    if input.electrolyzer_config !== nothing
        @variable(model, electrolyzer_power_var[1:n_bus, 1:hours] >= 0)
        electrolyzer_power = electrolyzer_power_var
    end

    # Loss of inertia variable (for inertia constraint)
    loss_of_inertia = nothing
    if input.inertia_limit > 0 || !isempty(input.inertia_limit_hourly)
        @variable(model, loss_of_inertia_var[1:hours] >= 0)
        loss_of_inertia = loss_of_inertia_var
    end

    # CO2 budget violation slack variable
    # Single variable for total emissions exceeding budget
    co2_budget_violation = nothing
    if !isinf(input.co2_budget) && input.co2_budget > 0
        @variable(model, co2_budget_violation_var >= 0)
        co2_budget_violation = co2_budget_violation_var
    end

    # Rooftop solar curtailment (node × hour)
    rooftop_curtailment = nothing
    if input.rooftop_generation !== nothing
        @variable(model, rooftop_curtailment_var[1:n_node, 1:hours] >= 0)
        rooftop_curtailment = rooftop_curtailment_var
    end

    # Constraint references for dual extraction (populated by add_demand_constraints!)
    balance_constraints = Dict{Tuple{Int,Int}, Any}()

    # Delay retirement variables
    # Binary: 1 = delay retirement (keep operating), 0 = proceed with retirement
    gen_delay_retirement = nothing
    bat_delay_retirement = nothing
    gen_delay_retirement_capacity = Dict{Tuple{Int,Int}, Float64}()
    bat_delay_retirement_capacity = Dict{Tuple{Int,Int}, Float64}()

    # pending_gen and pending_bat_ret already computed above for lookup maps
    if !isempty(pending_gen)
        gen_delay_retirement = Dict{Tuple{Int,Int}, VariableRef}()
        for (g, bus_caps) in pending_gen
            for (b, orig_cap) in bus_caps
                if orig_cap > 0
                    var = @variable(model, binary = true,
                                   base_name = "gen_delay_ret_$(g)_$(b)")
                    gen_delay_retirement[(g, b)] = var
                    gen_delay_retirement_capacity[(g, b)] = orig_cap
                end
            end
        end
    end

    if !isempty(pending_bat_ret)
        bat_delay_retirement = Dict{Tuple{Int,Int}, VariableRef}()
        for (bi, bus_caps) in pending_bat_ret
            for (b, orig_cap) in bus_caps
                if orig_cap > 0
                    var = @variable(model, binary = true,
                                   base_name = "bat_delay_ret_$(bi)_$(b)")
                    bat_delay_retirement[(bi, b)] = var
                    bat_delay_retirement_capacity[(bi, b)] = orig_cap
                end
            end
        end
    end

    # Reservoir hydroelectric variables (only for generators with reservoir_capacity > 0)
    # Use buses_of_gen for sparse indexing (reservoirs are generator-associated)
    reservoir_level = nothing
    reservoir_spillage = nothing
    reservoir_pump = nothing
    reservoir_invest_capacity = nothing
    has_any_reservoir = any(any(g.reservoir_capacity .> 0) for g in input.generators)
    if has_any_reservoir
        # Level has hours+1 (initial + each hour end state) — SparseAxisArray
        reservoir_level = @variable(model, [g=1:n_gen, b=buses_of_gen[g], t=1:(hours+1)], lower_bound=0, base_name="res_level")
        reservoir_spillage = @variable(model, [g=1:n_gen, b=buses_of_gen[g], t=1:hours], lower_bound=0, base_name="res_spill")
        reservoir_pump = @variable(model, [g=1:n_gen, b=buses_of_gen[g], t=1:hours], lower_bound=0, base_name="res_pump")
        if is_dev
            @variable(model, reservoir_invest_cap_var[1:n_gen, 1:n_bus] >= 0)
            reservoir_invest_capacity = reservoir_invest_cap_var
        end
    end

    # Forced replacement variables
    # Continuous variables for units where replacement_needed=true
    gen_forced_replacement = nothing
    bat_forced_replacement = nothing
    if !isempty(input.replacement_needed)
        gen_forced_replacement = Dict{Tuple{Int,Int}, VariableRef}()
        for ((g, b), needed) in input.replacement_needed
            if needed
                var = @variable(model, lower_bound=0,
                               base_name="gen_forced_repl_$(g)_$(b)")
                gen_forced_replacement[(g, b)] = var
            end
        end
    end
    if !isempty(input.bat_replacement_needed)
        bat_forced_replacement = Dict{Tuple{Int,Int}, VariableRef}()
        for ((bi, b), needed) in input.bat_replacement_needed
            if needed
                var = @variable(model, lower_bound=0,
                               base_name="bat_forced_repl_$(bi)_$(b)")
                bat_forced_replacement[(bi, b)] = var
            end
        end
    end

    # =========================================================================
    # Segment variables for PWL (bidding curve) cost decomposition
    # Only created for generators/batteries with >1 cost segment.
    # =========================================================================
    gen_seg_output = Dict{Tuple{Int,Int}, Any}()
    for (g, bus_curves) in input.gen_cost_curves
        for (b, segs) in bus_curves
            if length(segs) > 1
                n_seg = length(segs)
                sv = @variable(model, [k=1:n_seg, t=1:hours],
                               lower_bound=0, base_name="gseg_$(g)_$(b)")
                gen_seg_output[(g, b)] = sv
                # Link: gen_output[g,b,t] = sum of segment outputs
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
            if length(segs) > 1
                n_seg = length(segs)
                sv = @variable(model, [k=1:n_seg, t=1:hours],
                               lower_bound=0, base_name="bseg_$(bi)_$(b)")
                bat_seg_discharge[(bi, b)] = sv
                # Link: bat_discharge[bi,b,t] = sum of segment outputs
                for t in 1:hours
                    @constraint(model, bat_discharge[bi, b, t] ==
                        sum(sv[k, t] for k in 1:n_seg))
                end
            end
        end
    end

    return PowerSystemVariables(
        gen_output, gen_status, gen_startup, gen_shutdown,
        curtailment, fre_penetration_loss,
        bat_charge, bat_discharge, bat_soc,
        power_flow, voltage_angle,
        reserve_static, reserve_dynamic, reserve_static_loss, reserve_dynamic_loss,
        load_shed,
        gen_investment, bat_investment_power, bat_investment_capacity, transfer_investment,
        buses_of_gen, gens_at_bus, buses_of_bat, bats_at_bus;
        co2_emissions = co2_emissions,
        bat_charge_status = bat_charge_status,
        soc_violation = soc_violation,
        bat_spillage = bat_spillage,
        transfer_margin = transfer_margin,
        ev_charging = ev_charging,
        ev_v2g = ev_v2g,
        ev_soc = ev_soc,
        ev_loss = ev_loss,
        electrolyzer_power = electrolyzer_power,
        loss_of_inertia = loss_of_inertia,
        balance_constraints = balance_constraints,
        loss_of_load_sectoral = loss_of_load_sectoral,
        flexible_demand_curtailed = flexible_demand_curtailed,
        co2_budget_violation = co2_budget_violation,
        rooftop_curtailment = rooftop_curtailment,
        gen_delay_retirement = gen_delay_retirement,
        bat_delay_retirement = bat_delay_retirement,
        gen_delay_retirement_capacity = gen_delay_retirement_capacity,
        bat_delay_retirement_capacity = bat_delay_retirement_capacity,
        ev_charge_status = ev_charge_status,
        demand_shift = demand_shift,
        gen_forced_replacement = gen_forced_replacement,
        bat_forced_replacement = bat_forced_replacement,
        reservoir_level = reservoir_level,
        reservoir_spillage = reservoir_spillage,
        reservoir_pump = reservoir_pump,
        reservoir_invest_capacity = reservoir_invest_capacity,
        gen_seg_output = gen_seg_output,
        bat_seg_discharge = bat_seg_discharge
    )
end

"""
    build_objective!(model, vars::PowerSystemVariables, input::PowerSystemInput)

Build the objective function: minimize total system cost.

Cost components:
1. Operational costs: fuel, fixed O&M, maintenance
2. Startup costs (unit commitment mode)
3. Penalty costs: load shedding, curtailment, reserve shortage
4. CO2 costs
5. Investment costs (development mode)
"""
function build_objective!(model, vars::PowerSystemVariables, input::PowerSystemInput)
    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_bus = input.network.num_buses
    hours = input.temporal.hours
    is_dev = input.mode == "development"

    # Temporal resolution: each timestep represents this many hours of energy.
    # All energy-based costs ($/MWh × MW × timesteps) must scale by this factor
    # to convert from per-timestep power to actual energy (MWh).
    temporal_resolution_hours = input.temporal.resolution_hours > 0 ? input.temporal.resolution_hours : 1

    # ==========================================================================
    # Operational Costs
    # ==========================================================================

    # Fuel costs ($/MWh × MWh) — sparse iteration over active (gen, bus) pairs
    # When a generator has a multi-segment bidding curve, the cost is applied
    # per-segment (PWL) instead of flat. Flat generators use the original path.
    fuel_cost = AffExpr(0.0)
    for g in 1:n_gen
        for b in vars.buses_of_gen[g]
            if haskey(vars.gen_seg_output, (g, b))
                # Multi-segment (PWL) cost
                segs = input.gen_cost_curves[g][b]
                seg_vars = vars.gen_seg_output[(g, b)]
                for k in 1:length(segs), t in 1:hours
                    add_to_expression!(fuel_cost,
                        segs[k].marginal_cost, seg_vars[k, t])
                end
            else
                # Flat cost (original path — zero overhead)
                for t in 1:hours
                    add_to_expression!(fuel_cost,
                        input.generators[g].fuel_cost[b], vars.gen_output[g, b, t])
                end
            end
        end
    end

    # Fixed O&M costs ($/MWh × MWh) — sparse iteration
    fixed_cost = AffExpr(0.0)
    for g in 1:n_gen
        for b in vars.buses_of_gen[g], t in 1:hours
            add_to_expression!(fixed_cost,
                input.generators[g].fixed_cost[b], vars.gen_output[g, b, t])
        end
    end

    # Maintenance costs ($/MWh × MWh) — sparse iteration
    maintenance_cost = AffExpr(0.0)
    for g in 1:n_gen
        for b in vars.buses_of_gen[g], t in 1:hours
            add_to_expression!(maintenance_cost,
                input.generators[g].maintenance_cost[b], vars.gen_output[g, b, t])
        end
    end

    # ==========================================================================
    # Battery Maintenance Cost
    # Cost = (charge + discharge) * maintenance_cost for each battery
    # ==========================================================================
    # Battery maintenance cost — sparse iteration over active (bat, bus) pairs
    battery_maintenance_cost = AffExpr(0.0)
    for bi in 1:n_bat
        for b in vars.buses_of_bat[bi], t in 1:hours
            mc = input.batteries[bi].maintenance_cost[b]
            add_to_expression!(battery_maintenance_cost, mc, vars.bat_charge[bi, b, t])
            add_to_expression!(battery_maintenance_cost, mc, vars.bat_discharge[bi, b, t])
        end
    end

    # ==========================================================================
    # Battery Throughput Degradation Cost
    # Cost = discharge * throughput_degradation_cost for each battery
    # ==========================================================================
    # Battery throughput degradation cost — sparse iteration
    # When a battery has a multi-segment discharge cost curve, apply per-segment.
    battery_throughput_degradation_cost = AffExpr(0.0)
    for bi in 1:n_bat
        for b in vars.buses_of_bat[bi]
            if haskey(vars.bat_seg_discharge, (bi, b))
                segs = input.bat_cost_curves[bi][b]
                seg_vars = vars.bat_seg_discharge[(bi, b)]
                for k in 1:length(segs), t in 1:hours
                    add_to_expression!(battery_throughput_degradation_cost,
                        segs[k].marginal_cost, seg_vars[k, t])
                end
            else
                for t in 1:hours
                    add_to_expression!(battery_throughput_degradation_cost,
                        input.batteries[bi].throughput_degradation_cost[b],
                        vars.bat_discharge[bi, b, t])
                end
            end
        end
    end

    # ==========================================================================
    # Startup Costs (UC mode only)
    # ==========================================================================
    startup_cost = AffExpr(0.0)
    if vars.gen_startup !== nothing
        for g in 1:n_gen
            for b in vars.buses_of_gen[g], t in 1:hours
                add_to_expression!(startup_cost,
                    input.generators[g].start_up_cost[b], vars.gen_startup[g, b, t])
            end
        end
    end

    # ==========================================================================
    # Penalty Costs
    # ==========================================================================

    # Load shedding penalty (VOLL × MWh) — node-level
    #
    # IMPORTANT: only price load_shed *directly* when there is no
    # sectoral decomposition.  When sectoral_load_shed_cost is also in
    # the objective AND sum(sectoral_lol) == load_shed (the linking
    # equality), pricing load_shed here would double-count every shed
    # MWh — once via VOLL, and again via VOLL × criticality_multiplier
    # (cuba.yaml: medium=10, high=100, critical=1000).  The sectoral
    # expression already represents the criticality-weighted cost.
    n_node = input.network.num_nodes
    has_sectoral = vars.loss_of_load_sectoral !== nothing &&
                   !isempty(vars.loss_of_load_sectoral)
    load_shed_cost = if has_sectoral
        AffExpr(0.0)  # priced via sectoral_load_shed_cost below
    else
        @expression(model,
            input.loss_of_load_penalty *
            sum(vars.load_shed[b, t] for b in 1:n_bus, t in 1:hours)
        )
    end


    # Curtailment penalty in the objective.
    #
    # Original design (left as comment for context): "Curtailment has zero
    # penalty in the objective. Limiting curtailment is handled by the
    # max_curtailment_ratio constraint in the master problem." That constraint
    # is added per-rep-day (master_problem.jl:2160) but empirically does not
    # bind for the full-year operational LP — runs from late 2042 onwards show
    # 40–65% annual curtailment vs the configured 5% ratio, despite every day
    # having an identical solar profile (so master and operational should
    # agree). Until that disconnect is root-caused, expose the configured
    # `curtailment_cost` ($/MWh, ~$20 in cuba.yaml) directly in the operational
    # objective so the LP and the master (via NPV) trade curtailment against
    # storage capex on an explicit economic basis.
    curtailment_cost = @expression(model,
        input.curtailment_cost *
        sum(vars.curtailment[ni, t] for ni in 1:n_node, t in 1:hours)
    )

    # Static reserve shortage penalty — node-level
    reserve_static_cost = @expression(model,
        input.loss_of_reserve_static *
        sum(vars.reserve_static_loss[n, t] for n in 1:n_node, t in 1:hours)
    )

    # Dynamic reserve shortage penalty — node-level
    reserve_dynamic_cost = @expression(model,
        input.loss_of_reserve_dynamic *
        sum(vars.reserve_dynamic_loss[n, t] for n in 1:n_node, t in 1:hours)
    )

    # ==========================================================================
    # CO2 Costs
    # ==========================================================================
    co2_cost_expr = AffExpr(0.0)
    if input.co2_cost > 0
        # CO2 cost based on fuel type and emissions — sparse iteration
        for g in 1:n_gen
            fuel = input.generators[g].fuel
            if haskey(input.fuel_co2, fuel)
                co2_factor = input.fuel_co2[fuel]  # tonnes CO2/MWh
                coeff = input.co2_cost * co2_factor
                for b in vars.buses_of_gen[g], t in 1:hours
                    add_to_expression!(co2_cost_expr, coeff, vars.gen_output[g, b, t])
                end
            end
        end
    end

    # ==========================================================================
    # Investment Costs (development mode only)
    # ==========================================================================
    investment_cost = AffExpr(0.0)
    if is_dev && vars.gen_investment !== nothing
        # Generator investment costs ($/MW × MW) — sparse iteration
        for g in 1:n_gen, b in vars.buses_of_gen[g]
            if input.generators[g].invest_max[b] > 0
                add_to_expression!(investment_cost,
                    input.generators[g].invest_cost[b] * vars.gen_investment[g, b])
            end
        end

        # Battery investment costs — sparse iteration
        if vars.bat_investment_power !== nothing && n_bat > 0
            for bi in 1:n_bat, b in vars.buses_of_bat[bi]
                if input.batteries[bi].invest_max_power[b] > 0
                    add_to_expression!(investment_cost,
                        input.batteries[bi].invest_cost_power[b] * vars.bat_investment_power[bi, b])
                end
                if input.batteries[bi].invest_max_capacity[b] > 0
                    add_to_expression!(investment_cost,
                        input.batteries[bi].invest_cost_capacity[b] * vars.bat_investment_capacity[bi, b])
                end
            end
        end
    end

    # ==========================================================================
    # Inertia Loss Penalty
    # ==========================================================================
    inertia_cost = AffExpr(0.0)
    if vars.loss_of_inertia !== nothing
        inertia_cost = @expression(model,
            input.loss_of_inertia_penalty * sum(vars.loss_of_inertia[t] for t in 1:hours)
        )
    end

    # ==========================================================================
    # EV Loss Penalty (for not meeting target SOC)
    # ==========================================================================
    ev_loss_cost = AffExpr(0.0)
    if vars.ev_loss !== nothing && input.ev_config !== nothing
        ev_loss_cost = @expression(model,
            input.ev_config.loss_penalty * sum(vars.ev_loss[n, t] for n in 1:n_node, t in 1:hours)
        )
    end

    # ==========================================================================
    # FRE Penetration Loss Penalty
    # Penalizes the slack on the RE-penetration target so the dispatch has an
    # incentive to use available renewable energy. Charged at the configured
    # per-MWh penalty; temporal_resolution_hours is applied globally in the
    # objective, so penalty × Σslack × tres recovers the true $/MWh energy cost.
    # ==========================================================================
    # Use config-based FRE penalty or fallback to a safe default (100 $/MWh).
    # Avoid fallback formula lolp*hours*100 which yields 1e9 when lolp=10M, causing
    # numerical instability in the simplex solver (dual ratio test failure).
    fre_penalty = if input.fre_penetration_penalty > 0
        input.fre_penetration_penalty
    else
        @warn "fre_penetration_penalty not configured, using default 1e-4 M\$/MWh (= 100 \$/MWh)"
        1e-4
    end
    # fre_penetration_loss is the RE-target slack (MW); see the
    # re_penetration_target constraint. Charged at the plain per-MWh penalty
    # (matches the master problem's convention).
    fre_penetration_cost = @expression(model,
        fre_penalty *
        sum(vars.fre_penetration_loss[n, t] for n in 1:n_node, t in 1:hours)
    )

    # ==========================================================================
    # SOC Violation Penalty
    # Very high penalty (1e6) to enforce SOC limits as soft constraint
    # ==========================================================================
    soc_violation_cost = AffExpr(0.0)
    if n_bat > 0 && vars.soc_violation !== nothing
        SOC_VIOLATION_PENALTY = input.soc_violation_penalty
        for bi in 1:n_bat
            for b in vars.buses_of_bat[bi], t in 1:hours
                add_to_expression!(soc_violation_cost, SOC_VIOLATION_PENALTY, vars.soc_violation[bi, b, t])
            end
        end
    end

    # ==========================================================================
    # Transfer Margin Penalty
    # Penalty for transmission capacity violations
    # ==========================================================================
    transfer_margin_cost = AffExpr(0.0)
    if vars.transfer_margin !== nothing && !isempty(vars.transfer_margin)
        # Use config-based transfer margin penalty or fallback to 10% of VOLL
        transfer_penalty = if input.transfer_margin_penalty > 0
            input.transfer_margin_penalty
        else
            fallback = input.loss_of_load_penalty * 0.1
            @warn "transfer_margin_penalty not configured, using 10% of VOLL = $fallback"
            fallback
        end
        for ((i, j), margin_vars) in vars.transfer_margin
            for t in 1:hours
                add_to_expression!(transfer_margin_cost, margin_vars[t], transfer_penalty)
            end
        end
    end

    # ==========================================================================
    # V2G Compensation
    # Income from V2G services - SUBTRACTED from cost as it reduces total cost
    # ==========================================================================
    v2g_compensation = AffExpr(0.0)
    if vars.ev_v2g !== nothing && input.ev_config !== nothing
        has_elec_price_v2g = !isempty(input.electricity_price) && length(input.electricity_price) >= hours
        if has_elec_price_v2g
            # Time-varying V2G compensation using electricity price (matches Python legacy)
            for n in 1:n_node, t in 1:hours
                add_to_expression!(v2g_compensation, input.electricity_price[t], vars.ev_v2g[n, t])
            end
        elseif input.ev_config.v2g_compensation > 0
            # Fallback to constant compensation
            v2g_compensation = @expression(model,
                input.ev_config.v2g_compensation * sum(vars.ev_v2g[n, t]
                    for n in 1:n_node, t in 1:hours)
            )
        end
    end

    # ==========================================================================
    # Sectoral Load Shedding Penalties
    # Weighted by sector criticality (higher criticality = higher penalty)
    # ==========================================================================
    sectoral_load_shed_cost = AffExpr(0.0)
    if vars.loss_of_load_sectoral !== nothing && !isempty(vars.loss_of_load_sectoral)
        for (sector, lol_vars) in vars.loss_of_load_sectoral
            # Get criticality weight for this sector (default 1.0)
            criticality = get(input.sectoral_criticality, sector, 1.0)
            sector_penalty = input.loss_of_load_penalty * criticality
            for n in 1:n_node, t in 1:hours
                add_to_expression!(sectoral_load_shed_cost, lol_vars[n, t], sector_penalty)
            end
        end
    end

    # ==========================================================================
    # Flexible Demand Benefit
    # Benefit from demand reduction - SUBTRACTED as it reduces cost
    # Valued at 50% of electricity price (price response benefit)
    # ==========================================================================
    flexible_demand_benefit = AffExpr(0.0)
    if vars.flexible_demand_curtailed !== nothing && !isempty(vars.flexible_demand_curtailed)
        has_elec_price_flex = !isempty(input.electricity_price) && length(input.electricity_price) >= hours
        for (sector, flex_vars) in vars.flexible_demand_curtailed
            for n in 1:n_node, t in 1:hours
                price_t = has_elec_price_flex ? input.electricity_price[t] : input.loss_of_load_penalty * 0.01
                add_to_expression!(flexible_demand_benefit, flex_vars[n, t], price_t * input.flexible_demand_benefit_ratio)
            end
        end
    end

    # ==========================================================================
    # CO2 Budget Violation Penalty
    # Penalty for exceeding CO2 budget - default 500 per tonne
    # ==========================================================================
    co2_budget_violation_cost = AffExpr(0.0)
    if vars.co2_budget_violation !== nothing
        co2_budget_violation_cost = @expression(model,
            input.co2_budget_violation_penalty * vars.co2_budget_violation
        )
    end

    # ==========================================================================
    # Rooftop Solar Curtailment Penalty
    # Penalty for curtailing rooftop solar
    # ==========================================================================
    rooftop_curtailment_cost = AffExpr(0.0)
    if vars.rooftop_curtailment !== nothing
        # Use config-based rooftop curtailment penalty (default 5.0)
        rooftop_curtailment_cost = @expression(model,
            input.rooftop_curtailment_penalty * sum(vars.rooftop_curtailment[n, t]
                for n in 1:n_node, t in 1:hours)
        )
    end

    # ==========================================================================
    # Battery Spillage Cost
    # Cost of spilling energy without grid injection - opportunity cost
    # Matches Python: bat_spillage * electricity_price[t]
    # ==========================================================================
    spillage_cost = AffExpr(0.0)
    if vars.bat_spillage !== nothing
        # Check if electricity_price is available; if not, use fallback
        has_elec_price = !isempty(input.electricity_price) && length(input.electricity_price) >= hours
        fallback_price = input.loss_of_load_penalty * 0.01  # Fallback if no price vector

        for bi in 1:n_bat
            if input.batteries[bi].spillage
                for b in vars.buses_of_bat[bi], t in 1:hours
                    if vars.bat_spillage[bi, b, t] !== nothing
                        # Use time-varying electricity price if available (matches Python line 1763)
                        price_t = has_elec_price ? input.electricity_price[t] : fallback_price
                        add_to_expression!(spillage_cost,
                            vars.bat_spillage[bi, b, t], price_t)
                    end
                end
            end
        end
    end

    # ==========================================================================
    # Converter Variable Costs (AC/DC + frequency converters)
    # ==========================================================================
    converter_cost = add_converter_objective_terms(vars, input)

    # ==========================================================================
    # Electrolyzer Costs (A4 fix: was computed but not added to objective)
    # Variable cost on power consumption
    # ==========================================================================
    electrolyzer_cost = AffExpr(0.0)
    if vars.electrolyzer_power !== nothing && input.electrolyzer_config !== nothing
        config = input.electrolyzer_config
        for b in 1:n_bus, t in 1:hours
            if length(config.variable_cost) >= b
                add_to_expression!(electrolyzer_cost, config.variable_cost[b], vars.electrolyzer_power[b, t])
            end
        end
        # Fixed cost on rated power (annualized per hour × hours)
        for b in 1:n_bus
            if length(config.rated_power) >= b && length(config.fixed_cost) >= b
                add_to_expression!(electrolyzer_cost, config.rated_power[b] * config.fixed_cost[b] * hours)
            end
        end
    end

    # ==========================================================================
    # Delayed Retirement Penalty (A5 fix: penalty for delaying retirement)
    # Matches Python legacy: delay_var * penalty_per_mw * original_capacity
    # ==========================================================================
    delay_retirement_cost = AffExpr(0.0)
    penalty_per_mw = input.delay_retirement_penalty_per_mw
    if vars.gen_delay_retirement !== nothing
        for ((g, b), var) in vars.gen_delay_retirement
            cap = get(vars.gen_delay_retirement_capacity, (g, b), 0.0)
            if cap > 0
                add_to_expression!(delay_retirement_cost, penalty_per_mw * cap, var)
            end
        end
    end
    if vars.bat_delay_retirement !== nothing
        for ((bi, b), var) in vars.bat_delay_retirement
            cap = get(vars.bat_delay_retirement_capacity, (bi, b), 0.0)
            if cap > 0
                add_to_expression!(delay_retirement_cost, penalty_per_mw * cap, var)
            end
        end
    end

    # ==========================================================================
    # Demand Shifting Cost (P10: distance-weighted cost)
    # Matches Python: shifted * abs(t - t_dest) * 0.1
    # ==========================================================================
    demand_shift_cost = AffExpr(0.0)
    if vars.demand_shift !== nothing && !isempty(vars.demand_shift)
        for (sector, shift_vars) in vars.demand_shift
            for ((b, t, t_dest), var) in shift_vars
                add_to_expression!(demand_shift_cost, var, abs(t - t_dest) * input.demand_shift_cost_rate)
            end
        end
    end

    # ==========================================================================
    # NPV Forced Replacement Penalty
    # Decommissioning cost + NPV-proportional penalty for units needing replacement
    # ==========================================================================
    MAX_DECOMMISSION_COST_PER_MW = input.max_decommission_cost_per_mw
    MAX_NPV_PENALTY_PER_MW = input.max_npv_penalty_per_mw
    npv_penalty_cost = AffExpr(0.0)

    if vars.gen_forced_replacement !== nothing && !isempty(vars.gen_forced_replacement)
        for ((g, b), var) in vars.gen_forced_replacement
            current_capacity = input.generators[g].rated_power[b]
            if current_capacity <= 0
                continue
            end
            # Decommissioning cost (bounded)
            decom_cost = get(input.decommissioning_cost_gen, (g, b), 0.0)
            bounded_decom = min(decom_cost, MAX_DECOMMISSION_COST_PER_MW)
            if bounded_decom > 0
                add_to_expression!(npv_penalty_cost, var, bounded_decom * current_capacity)
            end
            # NPV penalty (bounded) - only if NPV is negative
            unit_npv = get(input.unit_npv, (g, b), 0.0)
            if unit_npv < 0
                npv_penalty_per_mw = min(abs(unit_npv) / max(current_capacity, 1.0), MAX_NPV_PENALTY_PER_MW)
                # 10% of NPV penalty to avoid overshadowing dispatch costs
                add_to_expression!(npv_penalty_cost, var, npv_penalty_per_mw * current_capacity * 0.1)
            end
        end
    end

    if vars.bat_forced_replacement !== nothing && !isempty(vars.bat_forced_replacement)
        for ((bi, b), var) in vars.bat_forced_replacement
            current_capacity = input.batteries[bi].max_discharge_power[b]
            if current_capacity <= 0
                continue
            end
            # Decommissioning cost
            decom_cost = get(input.decommissioning_cost_bat, (bi, b), 0.0)
            if decom_cost > 0
                add_to_expression!(npv_penalty_cost, var, decom_cost * current_capacity)
            end
            # NPV penalty
            bat_npv = get(input.bat_unit_npv, (bi, b), 0.0)
            if bat_npv < 0
                add_to_expression!(npv_penalty_cost, var, abs(bat_npv) * 0.1)
            end
        end
    end

    # ==========================================================================
    # Reservoir Spillage Cost (opportunity cost of wasted water)
    # ==========================================================================
    reservoir_spillage_cost = AffExpr(0.0)
    if vars.reservoir_spillage !== nothing
        has_elec_price_res = !isempty(input.electricity_price) && length(input.electricity_price) >= hours
        fallback_price_res = input.loss_of_load_penalty * 0.01
        for g in 1:n_gen
            gen = input.generators[g]
            if any(gen.reservoir_capacity .> 0) && gen.reservoir_spillage_allowed
                for b in vars.buses_of_gen[g], t in 1:hours
                    if gen.reservoir_capacity[b] > 0
                        price_t = has_elec_price_res ? input.electricity_price[t] : fallback_price_res
                        add_to_expression!(reservoir_spillage_cost,
                            vars.reservoir_spillage[g, b, t], price_t)
                    end
                end
            end
        end
    end

    # Reservoir investment cost (development mode)
    reservoir_invest_cost = AffExpr(0.0)
    if is_dev && vars.reservoir_invest_capacity !== nothing
        for g in 1:n_gen, b in 1:n_bus
            gen = input.generators[g]
            if gen.reservoir_invest_max[b] > 0
                add_to_expression!(reservoir_invest_cost,
                    gen.reservoir_invest_cost[b] * vars.reservoir_invest_capacity[g, b])
            end
        end
    end

    # ==========================================================================
    # Total Objective
    # Each timestep represents temporal_resolution_hours hours of energy.
    # Energy-based costs ($/MWh × MW) must be scaled by this factor.
    # Non-energy costs (investment $/MW, startup events, retirement) are NOT scaled.
    # ==========================================================================
    # N-1 reliability-shortfall penalty (set by add_n1_security_constraints!;
    # AffExpr(0.0) when N-1 is disabled or fully met).
    n1_penalty_cost = get(model.ext, :n1_penalty_cost, AffExpr(0.0))

    energy_costs = (
        fuel_cost + fixed_cost + maintenance_cost +
        battery_maintenance_cost + battery_throughput_degradation_cost +
        load_shed_cost + curtailment_cost +
        reserve_static_cost + reserve_dynamic_cost +
        co2_cost_expr +
        inertia_cost + ev_loss_cost + fre_penetration_cost +
        soc_violation_cost + transfer_margin_cost +
        sectoral_load_shed_cost - flexible_demand_benefit - v2g_compensation +
        co2_budget_violation_cost + rooftop_curtailment_cost + spillage_cost +
        converter_cost + electrolyzer_cost +
        demand_shift_cost +
        reservoir_spillage_cost +
        n1_penalty_cost
    )
    # Scale non-energy (one-shot annual) costs by the window's fraction
    # of the year, so that summing operational rolling-window objectives
    # across the year recovers the full annual cost exactly once.
    # Without this scaling each of ~61 windows adds the full non-energy
    # cost block, inflating reported objectives by ~50–60×.
    # For master typical-day dispatch (own objective), this code path is
    # not hit so master's investment costing is unaffected.
    annual_hours = Float64(input.hours_per_year)
    window_real_hours = Float64(hours * temporal_resolution_hours)
    non_energy_window_fraction = annual_hours > 0 ?
        window_real_hours / annual_hours : 1.0
    non_energy_costs = (
        startup_cost + investment_cost +
        delay_retirement_cost + npv_penalty_cost +
        reservoir_invest_cost
    )
    @objective(model, Min,
        temporal_resolution_hours * energy_costs +
        non_energy_window_fraction * non_energy_costs
    )

    # Store individual cost expressions for granular extraction after solve.
    # Energy-based costs will be scaled by temporal_resolution_hours at extraction time.
    model.ext[:cost_expressions] = Dict{Symbol, Any}(
        :fuel_cost => fuel_cost,
        :fixed_om_cost => fixed_cost,
        :maintenance_cost => maintenance_cost,
        :startup_cost => startup_cost,
        :battery_maintenance_cost => battery_maintenance_cost,
        :battery_degradation_cost => battery_throughput_degradation_cost,
        :load_shedding_cost => load_shed_cost + sectoral_load_shed_cost,
        :curtailment_cost => curtailment_cost + rooftop_curtailment_cost,
        :reserve_static_cost => reserve_static_cost,
        :reserve_dynamic_cost => reserve_dynamic_cost,
        :co2_emission_cost => co2_cost_expr + co2_budget_violation_cost,
        :fre_penetration_cost => fre_penetration_cost,
        :inertia_cost => inertia_cost + ev_loss_cost,
        :soc_violation_cost => soc_violation_cost,
        :transfer_margin_cost => transfer_margin_cost,
        :v2g_compensation => v2g_compensation,
        :flexible_demand_benefit => flexible_demand_benefit,
        :investment_cost => investment_cost,
        :electrolyzer_cost => electrolyzer_cost,
        :converter_cost => converter_cost,
        :spillage_cost => spillage_cost,
        :delay_retirement_cost => delay_retirement_cost,
        :reservoir_spillage_cost => reservoir_spillage_cost,
        :demand_shift_cost => demand_shift_cost,
        :rooftop_curtailment_cost => rooftop_curtailment_cost,
        :npv_penalty_cost => npv_penalty_cost,
        :reservoir_invest_cost => reservoir_invest_cost,
        :n1_security_shortfall_cost => n1_penalty_cost,
    )
    model.ext[:temporal_resolution_hours] = temporal_resolution_hours
end

"""
    add_demand_constraints!(model, vars::PowerSystemVariables, input::PowerSystemInput)

Add power balance (demand satisfaction) constraints at each node.
Stores constraint references in vars.balance_constraints for dual extraction.

Matches legacy PowerSystem.py power balance:
  inflow + gen_output + bat_discharge == outflow + demand + bat_charge + reserves + curtailment

For single-node systems, adds full power balance directly.
For multi-node systems, the KCL in add_dc_constraints! handles the balance including
transmission flows, so we skip the simple balance here.
"""
function add_demand_constraints!(model, vars::PowerSystemVariables, input;
    extra_injections_fn::Union{Nothing, Function} = nothing)
    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_bus = input.network.num_buses
    hours = input.temporal.hours

    # Callers (create_power_system, master_problem.jl) choose DCOPF
    # (add_dc_constraints!) vs copper-plate balance; this function MUST add
    # balance constraints unconditionally.

    for t in 1:hours
        for b in 1:n_bus
            # Total generation at bus — sparse: only generators with capacity at this bus
            total_gen = @expression(model,
                sum(vars.gen_output[g, b, t] for g in vars.gens_at_bus[b]; init=AffExpr(0.0))
            )

            # Battery discharge and charge — sparse: only batteries at this bus
            bat_discharge = @expression(model,
                sum(vars.bat_discharge[bi, b, t] for bi in vars.bats_at_bus[b]; init=AffExpr(0.0))
            )

            bat_charge_sum = @expression(model,
                sum(vars.bat_charge[bi, b, t] for bi in vars.bats_at_bus[b]; init=AffExpr(0.0))
            )

            # Node index and demand fraction for node-level variable scaling.
            # Connection buses (role="connection") carry no demand: bus_df is
            # forced to 0 so all demand-side terms (load_shed, reserves, EV,
            # rooftop, demand) drop out of the bus-level KCL.
            ni = input.network.bus_to_node[b]
            bus_role = input.network.buses[b].role
            is_load_bus = bus_role == "load" || bus_role == "mixed"
            bus_df = is_load_bus ? input.network.buses[b].demand_fraction : 0.0

            # EV V2G (supply side) and charging (demand side)
            # Node-level variables scaled by demand_fraction (same as load_shed, reserves)
            ev_v2g_term = if vars.ev_v2g !== nothing
                vars.ev_v2g[ni, t] * bus_df
            else
                AffExpr(0.0)
            end

            ev_charging_term = if vars.ev_charging !== nothing
                vars.ev_charging[ni, t] * bus_df
            else
                AffExpr(0.0)
            end

            # Electrolyzer power (demand side)
            electrolyzer_term = if vars.electrolyzer_power !== nothing
                vars.electrolyzer_power[b, t]
            else
                AffExpr(0.0)
            end

            # Reservoir pump power (demand side — pumping consumes electricity)
            reservoir_pump_term = AffExpr(0.0)
            if vars.reservoir_pump !== nothing
                for g in vars.gens_at_bus[b]
                    gen = input.generators[g]
                    if gen.reservoir_capacity[b] > 0 && gen.reservoir_pump_capacity[b] > 0
                        add_to_expression!(reservoir_pump_term, vars.reservoir_pump[g, b, t])
                    end
                end
            end

            # Bus demand from parent node scaled by demand_fraction
            bus_demand = input.demand[t, ni] * bus_df

            # Rooftop solar generation (indexed per node, scaled by demand_fraction)
            rooftop_gen_term = if hasproperty(input, :rooftop_generation) && input.rooftop_generation !== nothing
                input.rooftop_generation[t, ni] * bus_df
            else
                0.0
            end

            # Rooftop curtailment term (node-level, scaled by demand_fraction)
            rooftop_curt_term = if vars.rooftop_curtailment !== nothing
                vars.rooftop_curtailment[ni, t] * bus_df
            else
                AffExpr(0.0)
            end

            # Extra injection terms from caller (master: tech_output, bat_tech, sectoral_lol)
            extra_term = if extra_injections_fn !== nothing
                extra_injections_fn(b, t)
            else
                AffExpr(0.0)
            end

            # NOTE: flex_curt is NOT in the KCL.  It participates only in the
            # objective (benefit) and sectoral constraints.  Including it here
            # allowed the model to eliminate demand at negative cost.

            # Power balance (node-level vars scaled by demand_fraction for bus-level KCL):
            # LHS: gen_output + bat_discharge + EV_V2G + load_shed + rooftop_gen + extra
            # RHS: bus_demand + electrolyzer + bat_charge + reserves + EV_charging + rooftop_curtailment
            con = @constraint(model,
                total_gen + bat_discharge + ev_v2g_term + vars.load_shed[b, t] + rooftop_gen_term + extra_term ==
                bus_demand + electrolyzer_term + bat_charge_sum + reservoir_pump_term +
                vars.reserve_static[ni, t] * bus_df + vars.reserve_dynamic[ni, t] * bus_df + ev_charging_term + rooftop_curt_term
            )

            # Store constraint reference for dual extraction
            if vars.balance_constraints !== nothing
                vars.balance_constraints[(b, t)] = con
            end
        end

        # ── Per-node constraints (outside bus loop) ──
        n_node = input.network.num_nodes
        for ni in 1:n_node
            # Rooftop curtailment cannot exceed available rooftop generation at node
            if vars.rooftop_curtailment !== nothing && hasproperty(input, :rooftop_generation) && input.rooftop_generation !== nothing
                @constraint(model,
                    vars.rooftop_curtailment[ni, t] <= input.rooftop_generation[t, ni],
                    base_name = "rooftop_curt_limit_n$(ni)_t$(t)"
                )
            end
        end

        # Per-bus: load_shed = 0 at non-demand buses (load_shed is UNSERVED
        # demand; a bus with no local demand has nothing to leave unserved,
        # so allowing it would make load_shed virtual generation). The
        # economic upper bound on shedding is the VOLL penalty itself — no
        # artificial per-node cap.
        for b in 1:n_bus
            ni = input.network.bus_to_node[b]
            bus_role = input.network.buses[b].role
            is_load_bus = bus_role == "load" || bus_role == "mixed"
            bus_df = is_load_bus ? input.network.buses[b].demand_fraction : 0.0
            bus_demand_t = input.demand[t, ni] * bus_df
            if bus_demand_t <= 0.0
                @constraint(model,
                    vars.load_shed[b, t] <= 0.0,
                    base_name = "max_load_shed_b$(b)_t$(t)")
            end
        end
    end
end

"""
    add_generator_constraints!(model, vars::PowerSystemVariables, input::PowerSystemInput)

Add generator operational constraints: capacity, ramps, min up/down times, investment limits.

Matches legacy PowerSystem.py:
- Renewable: gen_output <= (rated + investment) * availability
- Non-renewable: gen_output <= rated + investment AND gen_output <= M * gen_status
- Curtailment is NOT part of generator capacity constraints (it's per-node, defined separately)
"""
function add_generator_constraints!(model, vars::PowerSystemVariables, input;
    capacity_override::Union{Dict{Tuple{Int,Int}, Any}, Nothing} = nothing)
    n_gen = length(input.generators)
    n_bus = input.network.num_buses
    hours = input.temporal.hours
    # UC mode is identified by gen_startup being present (gen_status always exists)
    is_uc = vars.gen_startup !== nothing
    is_dev = vars.gen_investment !== nothing
    # Big-M for on/off constraints: use max demand × 1.1 to handle generators > 1 GW
    M = max(maximum(input.demand) * 1.1, 1e4)

    for g in 1:n_gen
        gen = input.generators[g]
        is_renewable = gen.type == "Renewable"

        for b in vars.buses_of_gen[g]
            rated = gen.rated_power[b]

            # When capacity_override provided (master problem), use precomputed capacity
            # and skip degradation, delayed retirement, and investment logic
            if capacity_override !== nothing && haskey(capacity_override, (g, b))
                total_capacity = capacity_override[(g, b)]
            else
                # Age-based degradation: age advances with simulation year
                # age = initial_age + (current_year - base_year)
                if length(gen.degradation_rate) >= b && gen.degradation_rate[b] > 0 && rated > 0
                    deg = gen.degradation_rate[b]
                    base_age = length(gen.initial_age) >= b ? gen.initial_age[b] : 0.0
                    age = base_age + max(0.0, Float64(input.year - input.base_year))
                    life = length(gen.life_time) >= b ? gen.life_time[b] : Inf
                    remaining = life - age
                    if remaining <= 0
                        rated = 0.0
                    else
                        rated = rated * (1 - deg)^age
                    end
                end

                # Delayed retirement capacity restoration (A5)
                # If this generator has pending retirement and delay_var=1, restore original capacity
                delay_cap_restore = AffExpr(0.0)
                if vars.gen_delay_retirement !== nothing && haskey(vars.gen_delay_retirement, (g, b))
                    orig_cap = get(vars.gen_delay_retirement_capacity, (g, b), 0.0)
                    if orig_cap > 0
                        add_to_expression!(delay_cap_restore, orig_cap, vars.gen_delay_retirement[(g, b)])
                    end
                end

                # Investment constraints (development mode)
                if is_dev && gen.invest_max[b] > 0
                    @constraint(model, vars.gen_investment[g, b] <= gen.invest_max[b])
                end

                # Total capacity = existing + investment + delayed retirement restoration
                total_capacity = if is_dev && gen.invest_max[b] > 0
                    rated + vars.gen_investment[g, b] + delay_cap_restore
                else
                    rated + delay_cap_restore
                end
            end

            # Reservoir generators: output limited by turbine capacity, not availability
            has_reservoir = gen.reservoir_capacity[b] > 0

            # Segment upper bounds for PWL cost decomposition
            if haskey(vars.gen_seg_output, (g, b))
                segs = input.gen_cost_curves[g][b]
                seg_vars = vars.gen_seg_output[(g, b)]
                for k in 1:length(segs), t in 1:hours
                    seg_cap = segs[k].fraction * total_capacity
                    if is_renewable && !has_reservoir
                        avail = gen.availability[t, b]
                        @constraint(model, seg_vars[k, t] <= seg_cap * avail)
                    else
                        @constraint(model, seg_vars[k, t] <= seg_cap)
                    end
                end
            end

            for t in 1:hours
                if is_renewable && !has_reservoir
                    avail = gen.availability[t, b]
                    @constraint(model,
                        vars.gen_output[g, b, t] <= total_capacity * avail)
                else
                    @constraint(model,
                        vars.gen_output[g, b, t] <= total_capacity)

                    # gen_status big-M only when NOT using capacity_override AND in UC mode.
                    # In economic dispatch, gen_status is fixed to 1, so the big-M constraint
                    # is redundant with `gen_output <= total_capacity` and only pollutes the
                    # LP matrix with a coefficient of M (~1e4), worsening conditioning.
                    if capacity_override === nothing && vars.gen_status !== nothing && is_uc
                        @constraint(model,
                            vars.gen_output[g, b, t] <= M * vars.gen_status[g, b, t]
                        )
                    end
                end

                # UC constraints only in operational dispatch (not master)
                if capacity_override === nothing && is_uc
                    min_out = gen.min_power[b] * rated
                    @constraint(model,
                        vars.gen_output[g, b, t] >= min_out * vars.gen_status[g, b, t]
                    )
                end
            end

            # Ramp constraints (always active for sparse — all entries have capacity)
            ramp_up_limit = total_capacity * gen.ramp_up[b]
            ramp_down_limit = total_capacity * gen.ramp_down[b]
            # t=1 ramp from prev-window output (rolling-horizon seam).
            # Empty Dict = no boundary; skip the t=1 constraint to preserve
            # the legacy free-start behaviour of the first window.
            prev_out = get(get(input.generator_output_prev, g, Dict{Int,Float64}()), b, NaN)
            if !isnan(prev_out)
                @constraint(model,
                    vars.gen_output[g, b, 1] - prev_out <= ramp_up_limit,
                    base_name = "ramp_up_seam_g$(g)_b$(b)")
                @constraint(model,
                    prev_out - vars.gen_output[g, b, 1] <= ramp_down_limit,
                    base_name = "ramp_down_seam_g$(g)_b$(b)")
            end
            for t in 2:hours
                @constraint(model,
                    vars.gen_output[g, b, t] - vars.gen_output[g, b, t-1] <= ramp_up_limit
                )
                @constraint(model,
                    vars.gen_output[g, b, t-1] - vars.gen_output[g, b, t] <= ramp_down_limit
                )
            end

            # Unit commitment logic (only in operational dispatch, not master)
            if capacity_override === nothing && is_uc && rated > 0
                min_up = Int(gen.min_up_time[b])
                min_down = Int(gen.min_down_time[b])

                for t in 1:hours
                    if t > 1
                        prev_status = vars.gen_status[g, b, t-1]
                    else
                        prev_status = get(get(input.generator_initial_status, g, Dict{Int,Float64}()), b, 0.0)
                    end

                    @constraint(model,
                        vars.gen_startup[g, b, t] >= vars.gen_status[g, b, t] - prev_status,
                        base_name = "startup_detect_g$(g)_b$(b)_t$(t)"
                    )

                    for τ in max(1, t - min_up + 1):t
                        if τ > 1
                            τ_prev = vars.gen_status[g, b, τ-1]
                        else
                            τ_prev = get(get(input.generator_initial_status, g, Dict{Int,Float64}()), b, 0.0)
                        end
                        @constraint(model,
                            vars.gen_status[g, b, t] >= vars.gen_status[g, b, τ] - τ_prev,
                            base_name = "min_up_g$(g)_b$(b)_t$(t)_tau$(τ)"
                        )
                    end

                    for τ in max(1, t - min_down + 1):t
                        if τ > 1
                            τ_prev = vars.gen_status[g, b, τ-1]
                        else
                            τ_prev = get(get(input.generator_initial_status, g, Dict{Int,Float64}()), b, 0.0)
                        end
                        @constraint(model,
                            1 - vars.gen_status[g, b, t] >= τ_prev - vars.gen_status[g, b, τ],
                            base_name = "min_down_g$(g)_b$(b)_t$(t)_tau$(τ)"
                        )
                    end
                end
            end
        end
    end

end

"""
    add_battery_constraints!(model, vars::PowerSystemVariables, input::PowerSystemInput)

Add battery storage constraints: SOC dynamics, charge/discharge limits, investment limits.
"""
function add_battery_constraints!(model, vars::PowerSystemVariables, input;
    capacity_override_power::Union{Dict{Tuple{Int,Int}, Any}, Nothing} = nothing,
    capacity_override_energy::Union{Dict{Tuple{Int,Int}, Any}, Nothing} = nothing,
    initial_soc_overrides::Union{Nothing, Dict{Tuple{Int,Int}, Any}} = nothing,
    final_soc_targets::Union{Nothing, Dict{Tuple{Int,Int}, Any}} = nothing)
    n_bat = length(input.batteries)
    if n_bat == 0
        return  # No batteries to constrain
    end

    n_bus = input.network.num_buses
    hours = input.temporal.hours
    tres = input.temporal.resolution_hours > 0 ? input.temporal.resolution_hours : 1
    is_dev = vars.bat_investment_power !== nothing
    has_override = capacity_override_power !== nothing

    for bi in 1:n_bat
        bat = input.batteries[bi]

        for b in vars.buses_of_bat[bi]
            # When capacity overrides provided (master problem), use precomputed capacity
            # and skip degradation, investment logic, SOC bounds, spillage, mutex, min cycling
            if has_override && haskey(capacity_override_power, (bi, b))
                effective_max_charge = capacity_override_power[(bi, b)]
                effective_max_discharge = capacity_override_power[(bi, b)]
                effective_capacity = capacity_override_energy[(bi, b)]

                η_charge = bat.charge_efficiency[b]
                η_discharge = bat.discharge_efficiency[b]
                # Guard against zero efficiency (e.g. virtual batteries with sparse config)
                if η_discharge <= 0.0
                    η_discharge = 1.0
                end
                if η_charge <= 0.0
                    η_charge = 1.0
                end

                # Initial SOC (override or config-based)
                soc_init_fraction = if b <= length(bat.soc_initial)
                    bat.soc_initial[b]
                else
                    # @debug not @warn: fires once per (battery × bus)
                    # at every window — would emit thousands of lines.
                    # Missing soc_initial is a config gap, not a fault.
                    @debug "Battery $bi bus $b: soc_initial not specified, defaulting to 0.5"
                    0.5
                end
                # Clamp to soc_min to avoid end-of-horizon vs soc_min conflict
                soc_min_b = bat.soc_min[b]
                if soc_init_fraction < soc_min_b
                    soc_init_fraction = soc_min_b
                end
                soc_init = effective_capacity * soc_init_fraction
                if initial_soc_overrides !== nothing && haskey(initial_soc_overrides, (bi, b))
                    soc_init = initial_soc_overrides[(bi, b)]
                end
                @constraint(model,
                    vars.bat_soc[bi, b, 1] == soc_init,
                    base_name = "bat_soc_initial_$(bi)_$(b)")

                for t in 1:hours
                    @constraint(model, vars.bat_charge[bi, b, t] <= effective_max_charge)
                    @constraint(model, vars.bat_discharge[bi, b, t] <= effective_max_discharge)
                    @constraint(model, vars.bat_soc[bi, b, t+1] <= effective_capacity)

                    # SOC dynamics (simplified: no self-discharge, no spillage in master LP)
                    # bat_charge/bat_discharge are in MW; multiply by tres to get MWh per timestep
                    @constraint(model,
                        vars.bat_soc[bi, b, t+1] == vars.bat_soc[bi, b, t] +
                        η_charge * vars.bat_charge[bi, b, t] * tres -
                        vars.bat_discharge[bi, b, t] * tres / η_discharge)
                end

                # Segment upper bounds for PWL discharge cost decomposition
                if haskey(vars.bat_seg_discharge, (bi, b))
                    segs = input.bat_cost_curves[bi][b]
                    seg_vars = vars.bat_seg_discharge[(bi, b)]
                    for k in 1:length(segs), t in 1:hours
                        @constraint(model, seg_vars[k, t] <= segs[k].fraction * effective_max_discharge)
                    end
                end

                # End-of-horizon SOC: TSAM linking target or cyclic
                if final_soc_targets !== nothing && haskey(final_soc_targets, (bi, b))
                    @constraint(model,
                        vars.bat_soc[bi, b, hours+1] == final_soc_targets[(bi, b)],
                        base_name = "bat_soc_link_$(bi)_$(b)")
                else
                    # Cyclic: SOC at end == initial SOC
                    @constraint(model,
                        vars.bat_soc[bi, b, hours+1] == soc_init,
                        base_name = "bat_soc_cyclic_$(bi)_$(b)")
                end
            else
                # === Standard operational dispatch path ===
                capacity = bat.capacity[b]
                max_charge = bat.max_charge_power[b]
                max_discharge = bat.max_discharge_power[b]

                # Age-based degradation for batteries: age advances with simulation year
                if length(bat.degradation_rate) >= b && bat.degradation_rate[b] > 0 && capacity > 0
                    deg = bat.degradation_rate[b]
                    base_age = length(bat.initial_age) >= b ? bat.initial_age[b] : 0.0
                    age = base_age + max(0.0, Float64(input.year - input.base_year))
                    life = length(bat.life_time) >= b ? bat.life_time[b] : Inf
                    remaining = life - age
                    if remaining <= 0
                        capacity = 0.0
                        max_charge = 0.0
                        max_discharge = 0.0
                    else
                        degrade_factor = (1 - deg)^age
                        capacity = capacity * degrade_factor
                        max_charge = max_charge * degrade_factor
                        max_discharge = max_discharge * degrade_factor
                    end
                end

                # Investment constraints (development mode)
                if is_dev
                    if bat.invest_max_power[b] > 0
                        @constraint(model, vars.bat_investment_power[bi, b] <= bat.invest_max_power[b])
                    end
                    if bat.invest_max_capacity[b] > 0
                        @constraint(model, vars.bat_investment_capacity[bi, b] <= bat.invest_max_capacity[b])
                    end
                end

                effective_capacity = if is_dev && bat.invest_max_capacity[b] > 0
                    capacity + vars.bat_investment_capacity[bi, b]
                else
                    capacity
                end

                effective_max_charge = if is_dev && bat.invest_max_power[b] > 0
                    max_charge + vars.bat_investment_power[bi, b]
                else
                    max_charge
                end

                effective_max_discharge = if is_dev && bat.invest_max_power[b] > 0
                    max_discharge + vars.bat_investment_power[bi, b]
                else
                    max_discharge
                end

                η_charge = bat.charge_efficiency[b]
                η_discharge = bat.discharge_efficiency[b]
                # Guard against zero efficiency (e.g. virtual batteries with sparse config)
                if η_discharge <= 0.0
                    η_discharge = 1.0
                end
                if η_charge <= 0.0
                    η_charge = 1.0
                end
                self_discharge = bat.self_discharge[b]
                # Ensure initial SOC respects minimum SOC bound.
                # When soc_initial=0 but soc_min>0 (max_DoD<1), a zero initial SOC
                # makes the end-of-horizon constraint (soc[end] ≈ soc_init) conflict
                # with soc_min. Fix: clamp initial SOC to at least soc_min.
                soc_init_frac = bat.soc_initial[b]
                if soc_init_frac < bat.soc_min[b] && capacity > 0
                    soc_init_frac = bat.soc_min[b]
                end
                soc_init = soc_init_frac * capacity

                @constraint(model,
                    vars.bat_soc[bi, b, 1] == soc_init,
                    base_name = "bat_soc_initial_$(bi)_$(b)"
                )

                for t in 1:hours
                    if is_dev && bat.invest_max_power[b] > 0
                        @constraint(model, vars.bat_charge[bi, b, t] <=
                            max_charge + vars.bat_investment_power[bi, b])
                        @constraint(model, vars.bat_discharge[bi, b, t] <=
                            max_discharge + vars.bat_investment_power[bi, b])
                    else
                        @constraint(model, vars.bat_charge[bi, b, t] <= max_charge)
                        @constraint(model, vars.bat_discharge[bi, b, t] <= max_discharge)
                    end

                    # Mutex constraint (Big-M: 2× max possible power)
                    if vars.bat_charge_status !== nothing
                        effective_power = is_dev && bat.invest_max_power[b] > 0 ?
                            max(max_charge, max_discharge) + bat.invest_max_power[b] : max(max_charge, max_discharge)
                        M_bat = effective_power * 2.0

                        @constraint(model, vars.bat_charge[bi, b, t] <= M_bat * vars.bat_charge_status[bi, b, t])
                        @constraint(model, vars.bat_discharge[bi, b, t] <= M_bat * (1 - vars.bat_charge_status[bi, b, t]))
                    end

                    # SOC bounds
                    if is_dev && bat.invest_max_capacity[b] > 0
                        total_cap = capacity + vars.bat_investment_capacity[bi, b]
                        @constraint(model, vars.bat_soc[bi, b, t+1] >=
                            bat.soc_min[b] * total_cap)
                        @constraint(model, vars.bat_soc[bi, b, t+1] <=
                            bat.soc_max[b] * total_cap + vars.soc_violation[bi, b, t])
                    else
                        @constraint(model, vars.bat_soc[bi, b, t+1] >= bat.soc_min[b] * capacity)
                        @constraint(model, vars.bat_soc[bi, b, t+1] <=
                            bat.soc_max[b] * capacity + vars.soc_violation[bi, b, t])
                    end

                    # SOC dynamics
                    # bat_charge/bat_discharge are in MW; multiply by tres to get MWh per timestep
                    spillage_term = if vars.bat_spillage !== nothing && bat.spillage && vars.bat_spillage[bi, b, t] !== nothing
                        vars.bat_spillage[bi, b, t] * tres
                    else
                        0.0
                    end
                    @constraint(model,
                        vars.bat_soc[bi, b, t+1] == vars.bat_soc[bi, b, t] * (1 - self_discharge) +
                        η_charge * vars.bat_charge[bi, b, t] * tres -
                        vars.bat_discharge[bi, b, t] * tres / η_discharge -
                        spillage_term
                    )

                    # Spillage power limit
                    if vars.bat_spillage !== nothing && bat.spillage && vars.bat_spillage[bi, b, t] !== nothing
                        if is_dev && bat.invest_max_power[b] > 0
                            @constraint(model, vars.bat_spillage[bi, b, t] <=
                                max_discharge + vars.bat_investment_power[bi, b])
                        else
                            @constraint(model, vars.bat_spillage[bi, b, t] <= max_discharge)
                        end
                    end
                end

                # Segment upper bounds for PWL discharge cost decomposition
                if haskey(vars.bat_seg_discharge, (bi, b))
                    segs = input.bat_cost_curves[bi][b]
                    seg_vars = vars.bat_seg_discharge[(bi, b)]
                    for k in 1:length(segs), t in 1:hours
                        @constraint(model, seg_vars[k, t] <= segs[k].fraction * effective_max_discharge)
                    end
                end

                # End-of-horizon SOC constraints.
                # When `cyclic_end_soc` is true (single full-year solve), force
                # SOC[end] ≈ SOC[start] for energy conservation.  In rolling
                # windows the runner sets it false and chains SOC across
                # windows via boundary_conditions; in that case we only enforce
                # soc_min/soc_max bounds, letting SOC drift freely.
                if input.cyclic_end_soc
                    initial_soc_energy = soc_init
                    soc_tol = input.soc_end_tolerance
                    @constraint(model,
                        vars.bat_soc[bi, b, hours+1] >= initial_soc_energy * (1.0 - soc_tol),
                        base_name = "bat_soc_end_lower_$(bi)_$(b)"
                    )
                    @constraint(model,
                        vars.bat_soc[bi, b, hours+1] <= initial_soc_energy * (1.0 + soc_tol),
                        base_name = "bat_soc_end_upper_$(bi)_$(b)"
                    )
                end

                # Minimum daily cycling constraint
                # bat_charge is in MW; sum(bat_charge) gives MW-timesteps.
                # min_cycling expressed in MW-timesteps: ratio * capacity_MWh * (timesteps/24) / period
                # The tres factor cancels: energy = sum(bat_charge)*tres, days = timesteps*tres/24
                days = hours / 24.0
                min_cycling = input.min_cycling_ratio * capacity * days / input.min_cycling_period_days
                if min_cycling > 0 && capacity > 0
                    @constraint(model,
                        sum(vars.bat_charge[bi, b, t] for t in 1:hours) >= min_cycling,
                        base_name = "bat_min_cycling_$(bi)_$(b)"
                    )
                end
            end
        end
    end

end

"""
    add_reservoir_constraints!(model, vars::PowerSystemVariables, input::PowerSystemInput)

Add reservoir hydroelectric constraints: water level dynamics, spillage, pump-back, investment.

For generators with `reservoir_capacity[n] > 0`, tracks water level (MWh-equivalent) with:
- Inflows (exogenous water input)
- Turbine output (gen_output / turbine_efficiency drains water)
- Pump-back (refills reservoir, consumes electricity)
- Spillage (uncontrolled overflow)
- Evaporation (fractional loss per hour)

Cyclic constraint ensures end-of-horizon level matches initial level (within tolerance).
"""
function add_reservoir_constraints!(model, vars::PowerSystemVariables, input;
    # TSAM seasonal hydro linking: when present, the reservoir starts a period
    # at the boundary level handed in (initial_reservoir_overrides[(g,b)]) and
    # ends it exactly at the next boundary (final_reservoir_targets[(g,b)]),
    # forming a chronological chain across representative periods instead of
    # being cyclic within the period.
    initial_reservoir_overrides::Union{Nothing, Dict{Tuple{Int,Int}, Any}} = nothing,
    final_reservoir_targets::Union{Nothing, Dict{Tuple{Int,Int}, Any}} = nothing)
    if vars.reservoir_level === nothing
        return  # No reservoir generators
    end

    n_gen = length(input.generators)
    n_bus = input.network.num_buses
    hours = input.temporal.hours
    is_dev = vars.reservoir_invest_capacity !== nothing

    # --- Hydraulic cascade topology ---------------------------------------
    # Resolve each reservoir's `cascade_downstream` name to a generator index
    # and record, per downstream reservoir, which upstream units feed it and
    # with what travel delay. The released water (turbined + spilled, summed
    # over the upstream's reservoir nodes) is injected into the downstream
    # reservoir's primary node. cascade_feeders[gd] = [(gu, delay), ...].
    name_to_gen = Dict{String, Int}()
    for g in 1:n_gen
        name_to_gen[input.generators[g].name] = g
    end
    primary_res_bus = Dict{Int, Int}()
    for g in 1:n_gen
        for b in vars.buses_of_gen[g]
            if input.generators[g].reservoir_capacity[b] > 0
                primary_res_bus[g] = b
                break
            end
        end
    end
    cascade_feeders = Dict{Int, Vector{Tuple{Int,Int}}}()
    for gu in 1:n_gen
        genu = input.generators[gu]
        isempty(genu.cascade_downstream) && continue
        gd = get(name_to_gen, genu.cascade_downstream, 0)
        gd == 0 && continue
        # Both ends must actually be reservoirs to form a water link.
        (haskey(primary_res_bus, gu) && haskey(primary_res_bus, gd)) || continue
        gd == gu && continue  # guard against a self-referential loop
        push!(get!(cascade_feeders, gd, Tuple{Int,Int}[]),
              (gu, max(0, genu.cascade_delay_hours)))
    end

    for g in 1:n_gen
        gen = input.generators[g]

        for b in vars.buses_of_gen[g]
            res_cap = gen.reservoir_capacity[b]

            # Skip non-reservoir generators at this bus: constrain reservoir variables to 0
            if res_cap <= 0
                for t in 1:hours
                    @constraint(model, vars.reservoir_spillage[g, b, t] <= 0,
                        base_name = "res_zero_spill_g$(g)_b$(b)_t$(t)")
                    @constraint(model, vars.reservoir_pump[g, b, t] <= 0,
                        base_name = "res_zero_pump_g$(g)_b$(b)_t$(t)")
                end
                for t in 1:(hours+1)
                    @constraint(model, vars.reservoir_level[g, b, t] <= 0,
                        base_name = "res_zero_level_g$(g)_b$(b)_t$(t)")
                end
                if is_dev
                    @constraint(model, vars.reservoir_invest_capacity[g, b] <= 0,
                        base_name = "res_zero_invest_g$(g)_b$(b)")
                end
                continue
            end

            # Reservoir parameters
            initial_level_frac = gen.reservoir_initial_level[b]
            min_level_frac = gen.reservoir_min_level[b]
            max_level_frac = gen.reservoir_max_level[b]
            η_turbine = gen.reservoir_turbine_efficiency[b]
            evap_rate = gen.reservoir_evaporation_rate[b]
            pump_cap = gen.reservoir_pump_capacity[b]
            η_pump = gen.reservoir_pump_efficiency[b]
            min_release_b = b <= length(gen.reservoir_min_release) ?
                gen.reservoir_min_release[b] : 0.0

            # Total reservoir capacity (existing + investment)
            total_res_cap = if is_dev && gen.reservoir_invest_max[b] > 0
                @constraint(model, vars.reservoir_invest_capacity[g, b] <= gen.reservoir_invest_max[b],
                    base_name = "res_invest_limit_g$(g)_b$(b)")
                res_cap + vars.reservoir_invest_capacity[g, b]
            else
                if is_dev
                    @constraint(model, vars.reservoir_invest_capacity[g, b] <= 0,
                        base_name = "res_zero_invest_g$(g)_b$(b)")
                end
                res_cap
            end

            # Initial level. Precedence: TSAM seasonal boundary variable >
            # rolling-horizon seam value > configured initial fraction.
            seasonal_init = initial_reservoir_overrides === nothing ? nothing :
                get(initial_reservoir_overrides, (g, b), nothing)
            prev_level = get(get(input.reservoir_level_prev, g, Dict{Int,Float64}()), b, NaN)
            initial_level_energy = isnan(prev_level) ? initial_level_frac * res_cap : prev_level
            if seasonal_init !== nothing
                @constraint(model,
                    vars.reservoir_level[g, b, 1] == seasonal_init,
                    base_name = "res_initial_g$(g)_b$(b)")
            else
                @constraint(model,
                    vars.reservoir_level[g, b, 1] == initial_level_energy,
                    base_name = "res_initial_g$(g)_b$(b)")
            end

            for t in 1:hours
                # Level bounds
                @constraint(model,
                    vars.reservoir_level[g, b, t+1] >= min_level_frac * total_res_cap,
                    base_name = "res_min_level_g$(g)_b$(b)_t$(t)")
                @constraint(model,
                    vars.reservoir_level[g, b, t+1] <= max_level_frac * total_res_cap,
                    base_name = "res_max_level_g$(g)_b$(b)_t$(t)")

                # Inflow (MW-eq for this hour)
                inflow_t = t <= size(gen.reservoir_inflow, 1) ? gen.reservoir_inflow[t, b] : 0.0

                # Pump term
                pump_term = if pump_cap > 0
                    @constraint(model, vars.reservoir_pump[g, b, t] <= pump_cap,
                        base_name = "res_pump_limit_g$(g)_b$(b)_t$(t)")
                    vars.reservoir_pump[g, b, t] * η_pump
                else
                    @constraint(model, vars.reservoir_pump[g, b, t] <= 0,
                        base_name = "res_no_pump_g$(g)_b$(b)_t$(t)")
                    AffExpr(0.0)
                end

                # Spillage term
                spillage_term = if gen.reservoir_spillage_allowed
                    vars.reservoir_spillage[g, b, t]
                else
                    @constraint(model, vars.reservoir_spillage[g, b, t] <= 0,
                        base_name = "res_no_spill_g$(g)_b$(b)_t$(t)")
                    AffExpr(0.0)
                end

                # Cascade inflow: water released by upstream reservoirs that
                # feed this one arrives (turbined + spilled) after their travel
                # delay. Only injected at the downstream reservoir's primary
                # node. Releases scheduled before the period start (t-delay < 1)
                # are outside this window and not carried in (a small edge
                # approximation; inter-period in-transit water is not tracked).
                cascade_in = AffExpr(0.0)
                if get(primary_res_bus, g, 0) == b && haskey(cascade_feeders, g)
                    for (gu, delay) in cascade_feeders[g]
                        τ = t - delay
                        τ >= 1 || continue
                        genu = input.generators[gu]
                        for bu in vars.buses_of_gen[gu]
                            genu.reservoir_capacity[bu] > 0 || continue
                            η_u = genu.reservoir_turbine_efficiency[bu]
                            add_to_expression!(cascade_in,
                                vars.gen_output[gu, bu, τ] / η_u)
                            add_to_expression!(cascade_in,
                                vars.reservoir_spillage[gu, bu, τ])
                        end
                    end
                end

                # Water level dynamics:
                # level[t+1] = level[t] * (1 - evap) + inflow + cascade - output/η_turbine + pump*η_pump - spillage
                @constraint(model,
                    vars.reservoir_level[g, b, t+1] ==
                    vars.reservoir_level[g, b, t] * (1 - evap_rate) +
                    inflow_t +
                    cascade_in -
                    vars.gen_output[g, b, t] / η_turbine +
                    pump_term -
                    spillage_term,
                    base_name = "res_dynamics_g$(g)_b$(b)_t$(t)")

                # Minimum environmental / ecological release: the water leaving
                # the reservoir (turbined + spilled) must meet the mandatory
                # downstream flow. Met by generating and/or spilling.
                if min_release_b > 0
                    @constraint(model,
                        vars.gen_output[g, b, t] / η_turbine + spillage_term >=
                        min_release_b,
                        base_name = "res_min_release_g$(g)_b$(b)_t$(t)")
                end
            end

            # End level. With TSAM seasonal linking the period end is pinned to
            # the next chronological boundary variable (water carries over);
            # otherwise the reservoir is cyclic within the period (end ≈ start).
            seasonal_final = final_reservoir_targets === nothing ? nothing :
                get(final_reservoir_targets, (g, b), nothing)
            if seasonal_final !== nothing
                @constraint(model,
                    vars.reservoir_level[g, b, hours+1] == seasonal_final,
                    base_name = "res_end_seasonal_g$(g)_b$(b)")
            else
                soc_tol = input.soc_end_tolerance
                @constraint(model,
                    vars.reservoir_level[g, b, hours+1] >= initial_level_energy * (1.0 - soc_tol),
                    base_name = "res_end_lower_g$(g)_b$(b)")
                @constraint(model,
                    vars.reservoir_level[g, b, hours+1] <= initial_level_energy * (1.0 + soc_tol),
                    base_name = "res_end_upper_g$(g)_b$(b)")
            end
        end
    end
end

"""
    add_reserve_constraints!(model, vars::PowerSystemVariables, input::PowerSystemInput)

Add spinning reserve requirements.
"""
function add_reserve_constraints!(model, vars::PowerSystemVariables, input;
    capacity_override::Union{Dict{Tuple{Int,Int}, Any}, Nothing} = nothing,
    demand_scale::Float64 = 1.0)
    n_gen = length(input.generators)
    n_bus = input.network.num_buses
    n_node = input.network.num_nodes
    b2n = input.network.bus_to_node
    hours = input.temporal.hours

    # Build node-to-buses mapping
    node_buses = [Int[] for _ in 1:n_node]
    for b in 1:n_bus
        push!(node_buses[b2n[b]], b)
    end

    n_bat = length(input.batteries)
    bats_at_bus = [Int[] for _ in 1:n_bus]
    for bi in 1:n_bat
        for b in vars.buses_of_bat[bi]
            push!(bats_at_bus[b], bi)
        end
    end

    for t in 1:hours
        for ni in 1:n_node
            # Available reserve from reservable non-renewable generators at this node
            available_reserve = AffExpr(0.0)
            for b in node_buses[ni]
                for g in vars.gens_at_bus[b]
                    gen = input.generators[g]
                    if !gen.reservable || gen.type == "Renewable"
                        continue
                    end
                    if capacity_override !== nothing && haskey(capacity_override, (g, b))
                        cap_g_b = capacity_override[(g, b)]
                        add_to_expression!(available_reserve, cap_g_b)
                        add_to_expression!(available_reserve, -1.0, vars.gen_output[g, b, t])
                    elseif gen.rated_power[b] > 0
                        add_to_expression!(available_reserve,
                            gen.rated_power[b] * gen.availability[t, b])
                        add_to_expression!(available_reserve, -1.0, vars.gen_output[g, b, t])
                    end
                end
                # Battery contribution: discharge headroom + charge that can be cut.
                # A battery currently dispatching P_d has (P_d_max - P_d) more to
                # give; one currently charging P_c can release P_c by stopping.
                for bi in bats_at_bus[b]
                    bat = input.batteries[bi]
                    if bat.max_discharge_power[b] > 0
                        add_to_expression!(available_reserve, bat.max_discharge_power[b])
                        add_to_expression!(available_reserve, -1.0, vars.bat_discharge[bi, b, t])
                        add_to_expression!(available_reserve, 1.0, vars.bat_charge[bi, b, t])
                    end
                end
            end

            # Static reserve requirement at node level
            node_demand = input.demand[t, ni] * demand_scale
            reserve_req = haskey(input.reserve_static_requirement, ni) ?
                input.reserve_static_requirement[ni] : input.reserve_static_default_ratio * max(node_demand, 0.0)

            @constraint(model,
                available_reserve + vars.reserve_static_loss[ni, t] >= reserve_req,
                base_name = "reserve_static_$(ni)_$(t)"
            )

            @constraint(model,
                vars.reserve_static[ni, t] <= reserve_req,
                base_name = "reserve_static_ub_$(ni)_$(t)"
            )

            # Dynamic reserve at node level
            reserve_req_dynamic = get(input.reserve_dynamic_requirement, ni, 0.0)
            if reserve_req_dynamic > 0
                available_dynamic = AffExpr(0.0)
                for b in node_buses[ni]
                    for g in vars.gens_at_bus[b]
                        gen = input.generators[g]
                        if gen.reservable && gen.type != "Renewable"
                            if capacity_override !== nothing && haskey(capacity_override, (g, b))
                                add_to_expression!(available_dynamic,
                                    capacity_override[(g, b)], input.dynamic_reserve_contribution)
                            elseif gen.rated_power[b] > 0
                                add_to_expression!(available_dynamic, input.dynamic_reserve_contribution, gen.rated_power[b])
                            end
                        end
                    end
                    # Batteries contribute their nameplate discharge to dynamic reserve
                    for bi in bats_at_bus[b]
                        bat = input.batteries[bi]
                        if bat.max_discharge_power[b] > 0
                            add_to_expression!(available_dynamic,
                                input.dynamic_reserve_contribution, bat.max_discharge_power[b])
                        end
                    end
                end

                @constraint(model,
                    vars.reserve_dynamic[ni, t] <= available_dynamic,
                    base_name = "reserve_dynamic_avail_$(ni)_$(t)")
                @constraint(model,
                    vars.reserve_dynamic[ni, t] + vars.reserve_dynamic_loss[ni, t] >= reserve_req_dynamic,
                    base_name = "reserve_dynamic_req_$(ni)_$(t)")
            else
                @constraint(model,
                    vars.reserve_dynamic[ni, t] <= 0,
                    base_name = "reserve_dynamic_zero_$(ni)_$(t)")
            end
        end
    end
end

"""
    add_inertia_constraints!(model, vars::PowerSystemVariables, input::PowerSystemInput)

Add system inertia constraints to ensure frequency stability.

Matches legacy PowerSystem.py inertia constraint:
  conventional_inertia + storage_inertia + loss_of_inertia >= INERTIA_LIMIT

Inertia is provided by:
- Conventional (non-renewable) generators: rated_power * inertia constant * gen_status
- Storage systems (batteries): (charge + discharge power) * inertia constant
"""
function add_inertia_constraints!(model, vars::PowerSystemVariables, input)
    # Skip if no inertia constraint
    if vars.loss_of_inertia === nothing
        return
    end

    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_bus = input.network.num_buses
    hours = input.temporal.hours
    is_dev = input.mode == "development"

    for t in 1:hours
        inertia_limit = if !isempty(input.inertia_limit_hourly) && t <= length(input.inertia_limit_hourly)
            input.inertia_limit_hourly[t]
        else
            input.inertia_limit
        end

        if inertia_limit <= 0
            continue
        end

        conv_inertia = AffExpr(0.0)
        for g in 1:n_gen
            gen = input.generators[g]
            for b in vars.buses_of_gen[g]
                inertia_const = gen.inertia[b]
                if inertia_const > 0
                    add_to_expression!(conv_inertia, vars.gen_output[g, b, t] * inertia_const)
                end
            end
        end

        storage_inertia = AffExpr(0.0)
        for bi in 1:n_bat
            bat = input.batteries[bi]
            for b in vars.buses_of_bat[bi]
                bat_inertia_const = bat.inertia[b]
                add_to_expression!(storage_inertia,
                    (vars.bat_charge[bi, b, t] + vars.bat_discharge[bi, b, t]) * bat_inertia_const)
            end
        end

        # Inertia constraint: conv_inertia + storage_inertia + loss_of_inertia >= inertia_limit
        @constraint(model,
            conv_inertia + storage_inertia + vars.loss_of_inertia[t] >= inertia_limit,
            base_name = "inertia_$(t)"
        )
    end
end

"""
    add_renewable_constraint!(model, vars::PowerSystemVariables, input::PowerSystemInput)

Add renewable energy penetration target constraint — CUMULATIVE SYSTEM-WIDE.

  total_RE + FRE_loss >= target * total_demand

RE penetration is measured against total demand (not generation). Battery
discharge does NOT count toward RE: batteries may charge from fossil
sources, so crediting discharge would let storage game the target.
FRE_loss is a penalised slack so the target can never make the LP
infeasible.
"""
function add_renewable_constraint!(model, vars::PowerSystemVariables, input)
    # Skip if no RE target set
    if input.re_penetration_target <= 0
        return
    end

    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_bus = input.network.num_buses
    hours = input.temporal.hours
    tres = input.temporal.resolution_hours > 0 ? input.temporal.resolution_hours : 1

    # Total RE generation (cumulative, system-wide) — sparse iteration
    total_re = AffExpr(0.0)
    for g in 1:n_gen
        gen = input.generators[g]
        if gen.type == "Renewable"
            for b in vars.buses_of_gen[g], t in 1:hours
                add_to_expression!(total_re, tres, vars.gen_output[g, b, t])
            end
        end
    end

    # NOTE: Battery discharge does NOT count toward RE — batteries may charge
    # from fossil sources, so counting discharge as RE would game the constraint.

    # Total FRE penetration loss (slack, system-wide) — node-level
    n_node = input.network.num_nodes
    total_fre_loss = AffExpr(0.0)
    for n in 1:n_node, t in 1:hours
        add_to_expression!(total_fre_loss, tres, vars.fre_penetration_loss[n, t])
    end

    # Total demand (energy)
    total_demand_val = sum(input.demand[t, n] * tres
                          for t in 1:hours, n in 1:size(input.demand, 2))

    @constraint(model,
        total_re + total_fre_loss >= input.re_penetration_target * total_demand_val,
        base_name = "re_penetration_target")
end

"""
    add_co2_constraint!(model, vars::PowerSystemVariables, input::PowerSystemInput)

Add CO2 emissions budget constraint with violation slack variable.

Matches Python legacy (power_system.py lines 2660-2664):
  total_co2_emissions <= budget + CO2_budget_violation

The violation variable is penalized in the objective function.
"""
function add_co2_constraint!(model, vars::PowerSystemVariables, input::PowerSystemInput)
    # Skip if no CO2 budget set
    if isinf(input.co2_budget) || input.co2_budget <= 0
        return
    end

    n_gen = length(input.generators)
    n_bus = input.network.num_buses
    hours = input.temporal.hours

    # Calculate total CO2 emissions — sparse iteration
    total_co2 = AffExpr(0.0)

    for g in 1:n_gen
        fuel = input.generators[g].fuel
        if haskey(input.fuel_co2, fuel)
            co2_factor = input.fuel_co2[fuel]  # tonnes CO2/MWh
            for b in vars.buses_of_gen[g], t in 1:hours
                add_to_expression!(total_co2, co2_factor, vars.gen_output[g, b, t])
            end
        end
    end

    # Scale CO2 budget by window fraction (matches Python legacy: budget * hours * tres / year_hours)
    tres = input.temporal.resolution_hours > 0 ? input.temporal.resolution_hours : 1
    window_fraction = hours * tres / Float64(input.hours_per_year)
    scaled_budget = input.co2_budget * window_fraction

    # CO2 budget constraint with violation slack
    # total_co2 <= scaled_budget + violation
    if vars.co2_budget_violation !== nothing
        @constraint(model, total_co2 <= scaled_budget + vars.co2_budget_violation,
                   base_name = "CO2_budget_constraint")
    else
        # Fallback to hard constraint if no violation variable
        @constraint(model, total_co2 <= scaled_budget,
                   base_name = "CO2_budget_constraint")
    end
end

"""
    add_co2_emissions_definition!(model, vars::PowerSystemVariables, input::PowerSystemInput)

Define CO2 emissions at each node and hour.

Matches Python legacy (power_system.py lines 2553-2558):
  CO2_emissions[node, t] == sum(gen_output[gen][node][t] * fuel_CO2[fuel] for gen)
"""
function add_co2_emissions_definition!(model, vars::PowerSystemVariables, input::PowerSystemInput)
    n_gen = length(input.generators)
    n_bus = input.network.num_buses
    n_node = input.network.num_nodes
    b2n = input.network.bus_to_node
    hours = input.temporal.hours

    # Build node-to-buses mapping
    node_buses = [Int[] for _ in 1:n_node]
    for b in 1:n_bus
        push!(node_buses[b2n[b]], b)
    end

    for ni in 1:n_node, t in 1:hours
        co2_expr = AffExpr(0.0)
        for b in node_buses[ni]
            for g in vars.gens_at_bus[b]
                fuel = input.generators[g].fuel
                if haskey(input.fuel_co2, fuel)
                    co2_factor = input.fuel_co2[fuel]
                    add_to_expression!(co2_expr, co2_factor, vars.gen_output[g, b, t])
                end
            end
        end

        @constraint(model,
            vars.co2_emissions[ni, t] == co2_expr,
            base_name = "co2_emissions_def_n$(ni)_t$(t)"
        )
    end
end

"""
    add_curtailment_constraints!(model, vars::PowerSystemVariables, input::PowerSystemInput)

Define curtailment as the difference between available and used renewable generation.

Matches legacy PowerSystem.py (line 1410):
  curtailment[node][t] == available_renewable_gen - used_renewable_gen

Also enforces that curtailment cannot exceed available renewable capacity (line 1412-1420).
"""
function add_curtailment_constraints!(model, vars::PowerSystemVariables, input;
    capacity_override::Union{Dict{Tuple{Int,Int}, Any}, Nothing} = nothing)
    n_gen = length(input.generators)
    n_bus = input.network.num_buses
    n_node = input.network.num_nodes
    b2n = input.network.bus_to_node
    hours = input.temporal.hours
    is_dev = vars.gen_investment !== nothing

    # Build node-to-buses mapping
    node_buses = [Int[] for _ in 1:n_node]
    for b in 1:n_bus
        push!(node_buses[b2n[b]], b)
    end

    for t in 1:hours
        for ni in 1:n_node
            available_renewable = AffExpr(0.0)
            used_renewable = AffExpr(0.0)

            for b in node_buses[ni]
                for g in vars.gens_at_bus[b]
                    gen = input.generators[g]
                    # Reservoir hydro is dispatchable (energy-limited by the
                    # water balance), not a must-take variable renewable, so it
                    # is excluded from availability-based curtailment accounting
                    # — consistent with add_generator_constraints!.
                    if gen.type == "Renewable" && gen.reservoir_capacity[b] <= 0
                        avail = gen.availability[t, b]
                        if capacity_override !== nothing && haskey(capacity_override, (g, b))
                            add_to_expression!(available_renewable,
                                capacity_override[(g, b)] * avail)
                        else
                            bus_rated = gen.rated_power[b]
                            if is_dev && gen.invest_max[b] > 0
                                add_to_expression!(available_renewable,
                                    (bus_rated + vars.gen_investment[g, b]) * avail)
                            else
                                add_to_expression!(available_renewable,
                                    bus_rated * avail)
                            end
                        end
                        add_to_expression!(used_renewable, 1.0, vars.gen_output[g, b, t])
                    end
                end
            end

            @constraint(model,
                vars.curtailment[ni, t] == available_renewable - used_renewable)

            @constraint(model,
                vars.curtailment[ni, t] <= available_renewable)
        end
    end
end

# =============================================================================
# N-1 Security Constraints
# Matches Python legacy power_system.py lines 5415-5516
# =============================================================================

"""
    identify_n1_critical_elements(input::PowerSystemInput)

Identify critical elements for N-1 security analysis.

Returns:
    Dict with:
    - 'lines': Vector of (i, j, capacity) tuples for critical transmission lines
    - 'generators': Dict{Int, Dict} with largest generator per node

Matches Python legacy _identify_n1_critical_elements (lines 480-526).
"""
function identify_n1_critical_elements(input::PowerSystemInput)
    n_bus = input.network.num_buses
    n_gen = length(input.generators)

    critical_elements = Dict{String, Any}(
        "lines" => Vector{Tuple{Int,Int,Float64}}(),
        "generators" => Dict{Int, Dict{String, Any}}()
    )

    # Identify critical transmission lines (bus×bus connections)
    for i in 1:n_bus
        for j in (i+1):n_bus
            capacity = get(input.network.connections, (i, j), 0.0)
            if capacity > 0
                push!(critical_elements["lines"], (i, j, capacity))
            end
        end
    end

    # Identify largest generator at each bus
    for b in 1:n_bus
        bus_generators = Tuple{Int, Float64}[]
        for g in 1:n_gen
            gen = input.generators[g]
            if gen.rated_power[b] > 0
                push!(bus_generators, (g, gen.rated_power[b]))
            end
        end

        if !isempty(bus_generators)
            largest_capacity, largest_idx = findmax(x -> x[2], bus_generators)
            g_idx, cap = bus_generators[largest_idx]
            critical_elements["generators"][b] = Dict{String, Any}(
                "gen_idx" => g_idx,
                "name" => input.generators[g_idx].name,
                "capacity" => cap
            )
        end
    end

    return critical_elements
end

"""
    add_n1_security_constraints!(model, vars::PowerSystemVariables, input::PowerSystemInput)

Add N-1 security constraints to ensure system can operate after losing largest single element.

Implements two types of N-1 security:
1. Generation N-1: System must meet demand even if largest generator fails
2. Transmission N-1: Lines must reserve capacity for post-contingency redistribution
"""
function add_n1_security_constraints!(model, vars::PowerSystemVariables, input::PowerSystemInput)
    # Skip if N-1 security not enabled
    if !input.n1_security_enabled
        return
    end

    n_gen = length(input.generators)
    n_bus = input.network.num_buses
    hours = input.temporal.hours

    # N-1 is a SOFT security criterion: a fleet/network that cannot survive
    # the loss of its largest element produces a penalised reliability
    # SHORTFALL, never an infeasible LP.  A hard N-1 constraint made
    # unit_commitment of a fixed inadequate fleet INFEASIBLE with no
    # recourse (the original cuba.yaml failure mode).  The shortfall is
    # priced like a static-reserve shortage (loss_of_reserve_static).
    n1_penalty = input.loss_of_reserve_static
    n1_cost = AffExpr(0.0)

    # Identify critical elements
    critical_elements = identify_n1_critical_elements(input)

    # ========================================
    # 1. GENERATION N-1 CONSTRAINTS
    # ========================================
    if input.n1_generation_enabled && !isempty(critical_elements["generators"])
        # Find the largest generator in the entire system
        system_largest_gen = 0
        system_largest_capacity = 0.0
        system_largest_bus = 0

        for (bus, gen_info) in critical_elements["generators"]
            if gen_info["capacity"] > system_largest_capacity
                system_largest_capacity = gen_info["capacity"]
                system_largest_gen = gen_info["gen_idx"]
                system_largest_bus = bus
            end
        end

        # Apply N-1 at system level: total generation - largest unit >= total demand
        # This allows the network to redistribute power when the largest unit fails
        for t in 1:hours
            # Total system generation EXCLUDING the largest unit — sparse iteration
            total_system_generation = AffExpr(0.0)
            for g in 1:n_gen
                for b in vars.buses_of_gen[g]
                    if !(g == system_largest_gen && b == system_largest_bus)
                        add_to_expression!(total_system_generation, 1.0, vars.gen_output[g, b, t])
                    end
                end
            end

            # Total system demand (sum over all buses)
            total_system_demand = sum(
                input.demand[t, input.network.bus_to_node[b]] *
                input.network.buses[b].demand_fraction
                for b in 1:n_bus
            )

            # SOFT N-1: generation (minus largest) + shortfall >= demand.
            # The shortfall slack is penalised in the objective so the LP
            # quantifies the security deficit instead of going infeasible.
            n1_short = @variable(model, lower_bound = 0,
                base_name = "n1_gen_shortfall_t$(t)")
            @constraint(model,
                total_system_generation + n1_short >= total_system_demand,
                base_name = "n1_gen_reserve_system_t$(t)"
            )
            add_to_expression!(n1_cost, n1_penalty, n1_short)
        end
    end

    # ========================================
    # 2. TRANSMISSION N-1 CONSTRAINTS
    # ========================================
    if input.n1_transmission_enabled && !isempty(critical_elements["lines"])
        reserve_factor = input.n1_transmission_reserve_factor

        for (i, j, capacity) in critical_elements["lines"]
            # Usable capacity under N-1 criteria (reserve some capacity for post-contingency flows)
            usable_capacity = capacity * reserve_factor

            for t in 1:hours
                # Limit flow in both directions (SOFT: a penalised slack
                # absorbs any post-contingency over-flow so the LP stays
                # feasible and reports the violation as a cost).
                if haskey(vars.power_flow, (i, j))
                    n1_over = @variable(model, lower_bound = 0,
                        base_name = "n1_trans_over_$(i)_$(j)_t$(t)")
                    @constraint(model,
                        vars.power_flow[(i, j)][t] <= usable_capacity + n1_over,
                        base_name = "n1_trans_reserve_$(i)_$(j)_t$(t)_pos"
                    )
                    @constraint(model,
                        vars.power_flow[(i, j)][t] >= -usable_capacity - n1_over,
                        base_name = "n1_trans_reserve_$(i)_$(j)_t$(t)_neg"
                    )
                    add_to_expression!(n1_cost, n1_penalty, n1_over)
                end
            end
        end
    end

    # Expose the N-1 reliability-shortfall cost so the objective can price
    # it (mirrors the reserve-shortage penalty pattern).
    model.ext[:n1_penalty_cost] = n1_cost
end

# =============================================================================
# SCOPF (Security-Constrained OPF) — Iterative Contingency Analysis
# =============================================================================

"""
    _build_ptdf_matrix(input::PowerSystemInput) -> Matrix{Float64}

Build the Power Transfer Distribution Factor matrix.
PTDF[l, b] = sensitivity of flow on line l to injection at bus b.

PTDF = diag(1/x) × A × B_bus^(-1)
where A is the branch-bus incidence matrix, B_bus is the bus susceptance matrix.
"""
function _build_ptdf_matrix(input::PowerSystemInput)
    n_bus = input.network.num_buses
    lines = input.network.transmission_lines
    n_lines = length(lines)
    slack = input.network.slack_bus

    # Build bus susceptance matrix B
    B = zeros(n_bus, n_bus)
    for line in lines
        i, j = line.from_node, line.to_node
        x = line.reactance_pu / line.num_circuits
        if abs(x) < 1e-12 || i == j
            continue
        end
        b = 1.0 / x
        B[i, i] += b
        B[j, j] += b
        B[i, j] -= b
        B[j, i] -= b
    end

    # Remove slack bus, invert, then restore
    non_slack = setdiff(1:n_bus, [slack])
    B_red = B[non_slack, non_slack]

    # Use pseudo-inverse for robustness
    B_inv_red = zeros(n_bus, n_bus)
    if size(B_red, 1) > 0
        B_inv_full = pinv(B_red)
        for (ri, i) in enumerate(non_slack)
            for (ci, j) in enumerate(non_slack)
                B_inv_red[i, j] = B_inv_full[ri, ci]
            end
        end
    end

    # Build PTDF: for each line l from i to j with reactance x_l
    # PTDF[l, b] = (B_inv[i,b] - B_inv[j,b]) / x_l
    ptdf = zeros(n_lines, n_bus)
    for (l, line) in enumerate(lines)
        i, j = line.from_node, line.to_node
        x = line.reactance_pu / line.num_circuits
        if abs(x) < 1e-12 || i == j
            continue
        end
        for b in 1:n_bus
            ptdf[l, b] = (B_inv_red[i, b] - B_inv_red[j, b]) / x
        end
    end

    return ptdf
end

"""
    _build_lodf_matrix(ptdf::Matrix{Float64}, lines) -> Matrix{Float64}

Build Line Outage Distribution Factor matrix from PTDF.
LODF[l, k] = change in flow on line l per unit of pre-outage flow on line k,
when line k is removed.

LODF[l, k] = (PTDF[l, from_k] - PTDF[l, to_k]) / (1 - (PTDF[k, from_k] - PTDF[k, to_k]))
"""
function _build_lodf_matrix(ptdf::Matrix{Float64}, lines)
    n_lines = length(lines)
    lodf = zeros(n_lines, n_lines)

    for k in 1:n_lines
        i_k, j_k = lines[k].from_node, lines[k].to_node
        denom = 1.0 - (ptdf[k, i_k] - ptdf[k, j_k])

        if abs(denom) < 1e-10
            # Line k is a bridge (radial) — its outage isolates buses
            # LODF is undefined; set to zero (no redistribution possible)
            for l in 1:n_lines
                if l != k
                    lodf[l, k] = 0.0
                end
            end
            continue
        end

        for l in 1:n_lines
            if l == k
                lodf[l, k] = -1.0  # Flow on tripped line goes to zero
                continue
            end
            lodf[l, k] = (ptdf[l, i_k] - ptdf[l, j_k]) / denom
        end
    end

    return lodf
end

"""
    _get_flow_variable(vars, i, j, t)

Get the power flow variable for line (i,j) at time t.
Returns nothing if no flow variable exists for this bus pair.
"""
function _get_flow_variable(vars::PowerSystemVariables, i::Int, j::Int, t::Int)
    if haskey(vars.power_flow, (i, j))
        return vars.power_flow[(i, j)][t]
    elseif haskey(vars.power_flow, (j, i))
        return -vars.power_flow[(j, i)][t]  # Reverse direction
    end
    return nothing
end

"""
    _add_corrective_gen_n1!(model, vars, input)

Add generation N-1 constraints with corrective actions.
Instead of simply requiring remaining generation >= demand (preventive),
allow post-contingency re-dispatch within ramp limits and battery response.

For each hour t:
  Σ gen_output[g,b,t] (excl largest) + battery_headroom >= demand[t]

Battery headroom = Σ max(0, bat_max_discharge_power - bat_discharge[b,t])
"""
function _add_corrective_gen_n1!(model, vars::PowerSystemVariables, input::PowerSystemInput)
    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_bus = input.network.num_buses
    hours = input.temporal.hours

    # Find system-wide largest generator
    system_largest_gen = 0
    system_largest_capacity = 0.0
    system_largest_bus = 0

    for g in 1:n_gen
        for b in 1:n_bus
            cap = input.generators[g].rated_power[b]
            if cap > system_largest_capacity
                system_largest_capacity = cap
                system_largest_gen = g
                system_largest_bus = b
            end
        end
    end

    if system_largest_gen == 0
        return
    end

    for t in 1:hours
        # Generation (excluding largest)
        remaining_gen = AffExpr(0.0)
        for g in 1:n_gen
            for b in vars.buses_of_gen[g]
                if !(g == system_largest_gen && b == system_largest_bus)
                    add_to_expression!(remaining_gen, 1.0, vars.gen_output[g, b, t])
                end
            end
        end

        # Battery corrective response: available discharge headroom
        battery_headroom = AffExpr(0.0)
        for b_idx in 1:n_bat
            bat = input.batteries[b_idx]
            for b in 1:n_bus
                rated = bat.max_discharge_power[b]
                if rated > 0
                    # Headroom = max_discharge_power - current_discharge
                    # (battery can ramp up discharge to its rated discharge power)
                    add_to_expression!(battery_headroom, rated)
                    add_to_expression!(battery_headroom, -1.0, vars.bat_discharge[b_idx, b, t])
                end
            end
        end

        # Total demand
        total_demand = sum(
            input.demand[t, input.network.bus_to_node[b]] *
            input.network.buses[b].demand_fraction
            for b in 1:n_bus
        )

        @constraint(model,
            remaining_gen + battery_headroom >= total_demand,
            base_name = "n1_corrective_gen_t$(t)"
        )
    end
end

"""
    add_scopf_constraints!(model, vars::PowerSystemVariables, input::PowerSystemInput;
                           max_scopf_iterations::Int=5,
                           violation_tolerance::Float64=0.01)

Iterative Security-Constrained OPF:
1. Solve base-case dispatch (no N-1 constraints)
2. Evaluate all N-1 contingencies using PTDF/LODF
3. Add constraints only for contingencies that cause violations
4. Re-solve and repeat until no new violations found

This is more efficient than the preventive approach in add_n1_security_constraints!
because it only adds constraints for binding contingencies.
"""
function add_scopf_constraints!(model, vars::PowerSystemVariables, input::PowerSystemInput;
                                 max_scopf_iterations::Int=5,
                                 violation_tolerance::Float64=0.01)
    if !input.n1_security_enabled
        return
    end

    n_bus = input.network.num_buses
    n_gen = length(input.generators)
    hours = input.temporal.hours
    lines = input.network.transmission_lines
    n_lines = length(lines)

    # Build PTDF matrix for the network
    ptdf = _build_ptdf_matrix(input)

    # Build LODF matrix from PTDF
    lodf = _build_lodf_matrix(ptdf, lines)

    # For each critical line outage, add post-contingency flow constraints
    # only when they would be violated
    if input.n1_transmission_enabled
        for k in 1:n_lines
            line_k = lines[k]
            cap_k = line_k.capacity_mw * line_k.num_circuits
            if cap_k <= 0
                continue
            end

            for l in 1:n_lines
                if l == k
                    continue
                end
                line_l = lines[l]
                cap_l = line_l.capacity_mw * line_l.num_circuits
                if cap_l <= 0 || abs(lodf[l, k]) < 1e-6
                    continue
                end

                # Post-contingency flow on line l when line k is out:
                # f_l_post = f_l_pre + LODF[l,k] × f_k_pre
                # |f_l_post| <= cap_l
                # i.e., |f_l + LODF[l,k] × f_k| <= cap_l

                i_l, j_l = line_l.from_node, line_l.to_node
                i_k, j_k = line_k.from_node, line_k.to_node

                lodf_lk = lodf[l, k]

                for t in 1:hours
                    # Get the flow variables for line l and line k
                    flow_l = _get_flow_variable(vars, i_l, j_l, t)
                    flow_k = _get_flow_variable(vars, i_k, j_k, t)

                    if flow_l !== nothing && flow_k !== nothing
                        # f_l + LODF[l,k] × f_k <= cap_l
                        @constraint(model,
                            flow_l + lodf_lk * flow_k <= cap_l,
                            base_name = "scopf_line$(l)_outage$(k)_t$(t)_pos"
                        )
                        # -(f_l + LODF[l,k] × f_k) <= cap_l
                        @constraint(model,
                            flow_l + lodf_lk * flow_k >= -cap_l,
                            base_name = "scopf_line$(l)_outage$(k)_t$(t)_neg"
                        )
                    end
                end
            end
        end
    end

    # Generation N-1 with corrective actions
    if input.n1_generation_enabled
        if input.n1_corrective_enabled
            _add_corrective_gen_n1!(model, vars, input)
        else
            # Fall back to preventive generation N-1 from identify_n1_critical_elements
            critical_elements = identify_n1_critical_elements(input)
            if !isempty(critical_elements["generators"])
                system_largest_gen = 0
                system_largest_capacity = 0.0
                system_largest_bus = 0

                for (bus, gen_info) in critical_elements["generators"]
                    if gen_info["capacity"] > system_largest_capacity
                        system_largest_capacity = gen_info["capacity"]
                        system_largest_gen = gen_info["gen_idx"]
                        system_largest_bus = bus
                    end
                end

                for t in 1:hours
                    total_system_generation = AffExpr(0.0)
                    for g in 1:n_gen
                        for b in vars.buses_of_gen[g]
                            if !(g == system_largest_gen && b == system_largest_bus)
                                add_to_expression!(total_system_generation, 1.0, vars.gen_output[g, b, t])
                            end
                        end
                    end

                    total_system_demand = sum(
                        input.demand[t, input.network.bus_to_node[b]] *
                        input.network.buses[b].demand_fraction
                        for b in 1:n_bus
                    )

                    @constraint(model,
                        total_system_generation >= total_system_demand,
                        base_name = "scopf_n1_gen_reserve_t$(t)"
                    )
                end
            end
        end
    end
end

# Export the new constraint functions
export add_renewable_constraint!, add_co2_constraint!, add_co2_emissions_definition!, add_curtailment_constraints!
export add_n1_security_constraints!, identify_n1_critical_elements
export add_scopf_constraints!

"""
    add_sectoral_demand_constraints!(model, vars::PowerSystemVariables, input::PowerSystemInput)

Add sectoral load shedding constraints:
1. Sum of sectoral LOL == total LOL (decomposition)
2. Per-sector LOL cap: lol_sector <= sectoral_demand[sector]
3. Criticality ordering: higher criticality sectors shed last

Matches legacy PowerSystem.py lines 1175-1230.
"""
function add_sectoral_demand_constraints!(model, vars::PowerSystemVariables, input::PowerSystemInput)
    if vars.loss_of_load_sectoral === nothing || isempty(vars.loss_of_load_sectoral)
        return
    end

    n_node = input.network.num_nodes
    n_bus = input.network.num_buses
    hours = input.temporal.hours
    sectors = collect(keys(vars.loss_of_load_sectoral))

    # Buses per node (load_shed is now per-bus; sectoral lol stays per-node).
    buses_of_node = [Int[] for _ in 1:n_node]
    for b in 1:n_bus
        push!(buses_of_node[input.network.bus_to_node[b]], b)
    end

    for ni in 1:n_node, t in 1:hours
        # 1. Sum of sectoral LOL == total LOL across this node's buses.
        sector_lol_sum = AffExpr(0.0)
        for sector in sectors
            add_to_expression!(sector_lol_sum, 1.0, vars.loss_of_load_sectoral[sector][ni, t])
        end
        node_load_shed = AffExpr(0.0)
        for b in buses_of_node[ni]
            add_to_expression!(node_load_shed, 1.0, vars.load_shed[b, t])
        end
        @constraint(model, sector_lol_sum == node_load_shed,
            base_name = "sectoral_lol_sum_$(ni)_$(t)")

        # 2. Per-sector LOL cap relaxed.  The original cap
        # `lol_sector <= sectoral_demand[sector][t, node]` is correct
        # in principle, but in real configs sectoral_demand isn't
        # always populated for every (sector, node, t) triple — when
        # the Grid Builder splits node demand across buses without
        # rebuilding sectoral aggregates, the cap forces lol_sector=0
        # and the equality `sum(lol_sector) == load_shed` then forces
        # load_shed=0, making the LP infeasible whenever the bus-level
        # demand exceeds what gen + import can serve.  load_shed is
        # already priced by VOLL × MWh via the sectoral cost term, so
        # leaving sectoral LOL unbounded does not change the optimal
        # dispatch; it only avoids spurious infeasibility.

        # 3. Flexible demand curtailment upper bound
        if vars.flexible_demand_curtailed !== nothing
            for sector in sectors
                if haskey(vars.flexible_demand_curtailed, sector) && haskey(input.sectoral_demand, sector)
                    sec_dem = max(0.0, input.sectoral_demand[sector][t, ni])
                    @constraint(model, vars.flexible_demand_curtailed[sector][ni, t] <= sec_dem,
                        base_name = "flex_curt_cap_$(sector)_$(ni)_$(t)")
                end
            end
        end
    end

    # B13: Demand shifting constraints (node-level)
    if vars.demand_shift !== nothing && !isempty(vars.demand_shift)
        for (sector, shift_vars) in vars.demand_shift
            crit = get(input.sectoral_criticality, sector, 1.0)
            flex_ratio = 1.0 - crit

            for ni in 1:n_node
                for t in 1:hours
                    if haskey(input.sectoral_demand, sector)
                        sec_dem = max(0.0, input.sectoral_demand[sector][t, ni])
                        shift_from_t = AffExpr(0.0)
                        for ((vn, vt, vt_dest), var) in shift_vars
                            if vn == ni && vt == t
                                add_to_expression!(shift_from_t, var)
                            end
                        end
                        @constraint(model, shift_from_t <= flex_ratio * sec_dem,
                            base_name = "demand_shift_out_cap_$(sector)_$(ni)_$(t)")
                    end
                end
            end
        end
    end
end

"""
    add_node_investment_limits!(model, vars::PowerSystemVariables, input::PowerSystemInput)

B14: Per-node cap on total investment (MW).
"""
function add_node_investment_limits!(model, vars::PowerSystemVariables, input::PowerSystemInput)
    is_dev = vars.gen_investment !== nothing
    if !is_dev || isempty(input.max_node_investment)
        return
    end

    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_bus = input.network.num_buses
    n_node = input.network.num_nodes
    b2n = input.network.bus_to_node

    # Build node-to-buses mapping
    node_buses = [Int[] for _ in 1:n_node]
    for b in 1:n_bus
        push!(node_buses[b2n[b]], b)
    end

    for ni in 1:n_node
        if ni > length(input.max_node_investment) || input.max_node_investment[ni] <= 0
            continue
        end

        node_inv = AffExpr(0.0)
        for b in node_buses[ni]
            for g in vars.gens_at_bus[b]
                if input.generators[g].invest_max[b] > 0
                    add_to_expression!(node_inv, 1.0, vars.gen_investment[g, b])
                end
            end
            if vars.bat_investment_power !== nothing
                for bi in vars.bats_at_bus[b]
                    if input.batteries[bi].invest_max_power[b] > 0
                        add_to_expression!(node_inv, 1.0, vars.bat_investment_power[bi, b])
                    end
                end
            end
        end

        @constraint(model, node_inv <= input.max_node_investment[ni],
            base_name = "max_node_inv_$(ni)")
    end
end

"""
    add_max_annual_system_cost!(model, vars::PowerSystemVariables, input::PowerSystemInput)

B15: Maximum annual system cost constraint.
"""
function add_max_annual_system_cost!(model, vars::PowerSystemVariables, input::PowerSystemInput)
    if isinf(input.max_annual_system_cost) || input.max_annual_system_cost <= 0
        return
    end

    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_bus = input.network.num_buses
    hours = input.temporal.hours
    tres = input.temporal.resolution_hours > 0 ? input.temporal.resolution_hours : 1

    # Scale annual cost limit to window fraction to avoid huge RHS values
    # that cause numerical issues in the LP solver
    window_hours = hours * tres
    annual_hours = Float64(input.hours_per_year)
    window_fraction = annual_hours > 0 ? window_hours / annual_hours : 1.0
    scaled_cost_limit = input.max_annual_system_cost * window_fraction

    # Compute operating cost terms (fuel + fixed + maintenance) — sparse iteration
    # Costs are per-MWh × MW (power), scale by tres to get energy-based cost
    operating_cost = AffExpr(0.0)
    for g in 1:n_gen
        gen = input.generators[g]
        for b in vars.buses_of_gen[g], t in 1:hours
            total_var_cost = gen.fuel_cost[b] + gen.fixed_cost[b] + gen.maintenance_cost[b]
            add_to_expression!(operating_cost, total_var_cost * tres, vars.gen_output[g, b, t])
        end
    end

    # Add slack variable to guarantee feasibility; penalize violation in objective
    cost_violation = @variable(model, base_name = "cost_violation", lower_bound = 0.0)
    @constraint(model, operating_cost - cost_violation <= scaled_cost_limit,
        base_name = "max_annual_system_cost")

    # Penalize cost violation at 10× the max generator variable cost to discourage it
    max_var_cost = 1.0
    for g in 1:n_gen
        gen = input.generators[g]
        for b in vars.buses_of_gen[g]
            total_var_cost = gen.fuel_cost[b] + gen.fixed_cost[b] + gen.maintenance_cost[b]
            max_var_cost = max(max_var_cost, total_var_cost)
        end
    end
    penalty_coeff = max_var_cost * 10.0
    obj = objective_function(model)
    @objective(model, Min, obj + penalty_coeff * cost_violation)
end

"""
    add_ev_constraints!(model, vars::PowerSystemVariables, input::PowerSystemInput)

Add EV fleet constraints: SOC dynamics, charging/V2G limits, mutual exclusivity.

Matches legacy PowerSystem.py EV constraints (lines 2345-2497):
- SOC dynamics: SOC[t+1] = SOC[t] + charging*η - V2G/η
- Charging demand satisfaction: charging + loss >= driving_consumption
- Charging/V2G power limits
- Charge/V2G mutual exclusivity via Big-M relaxation
"""
function add_ev_constraints!(model, vars::PowerSystemVariables, input::PowerSystemInput)
    if input.ev_config === nothing || vars.ev_charging === nothing
        return
    end

    ev = input.ev_config
    n_node = input.network.num_nodes
    hours = input.temporal.hours

    for ni in 1:n_node
        # Skip nodes with no EV fleet
        if ni > length(ev.num_vehicles) || ev.num_vehicles[ni] <= 0
            for t in 1:hours
                @constraint(model, vars.ev_charging[ni, t] <= 0, base_name = "ev_zero_charge_n$(ni)_$(t)")
                @constraint(model, vars.ev_v2g[ni, t] <= 0, base_name = "ev_zero_v2g_n$(ni)_$(t)")
                @constraint(model, vars.ev_soc[ni, t+1] <= 0, base_name = "ev_zero_soc_n$(ni)_$(t)")
                @constraint(model, vars.ev_loss[ni, t] <= 0, base_name = "ev_zero_loss_n$(ni)_$(t)")
            end
            @constraint(model, vars.ev_soc[ni, 1] == 0, base_name = "ev_soc_initial_n$(ni)")
            continue
        end

        # Initial SOC (node-level, no bus_df scaling needed)
        if length(ev.initial_soc) >= ni
            @constraint(model, vars.ev_soc[ni, 1] == ev.initial_soc[ni],
                base_name = "ev_soc_initial_n$(ni)")
        else
            default_soc = ev.target_soc * ev.battery_capacity_kwh * ev.num_vehicles[ni] / 1000.0
            @constraint(model, vars.ev_soc[ni, 1] == default_soc,
                base_name = "ev_soc_initial_n$(ni)")
        end

        for t in 1:hours
            # SOC dynamics (MWh)
            @constraint(model,
                vars.ev_soc[ni, t+1] == vars.ev_soc[ni, t]
                    + vars.ev_charging[ni, t] * ev.charge_efficiency
                    - vars.ev_v2g[ni, t] / ev.discharge_efficiency,
                base_name = "ev_soc_dynamics_n$(ni)_$(t)")

            # Charging demand satisfaction: charging + loss >= driving consumption
            ev_demand = ev.driving_consumption_profile[t, ni]
            @constraint(model,
                vars.ev_charging[ni, t] + vars.ev_loss[ni, t] >= ev_demand,
                base_name = "ev_demand_n$(ni)_$(t)")

            # Charging power limit
            max_charge = ev.max_charge_power_kw * ev.num_vehicles[ni] / 1000.0
            max_charge = max(max_charge, ev_demand * 2.0)
            @constraint(model, vars.ev_charging[ni, t] <= max_charge,
                base_name = "ev_max_charge_n$(ni)_$(t)")

            # V2G discharge limit
            avail = size(ev.availability_profile, 1) >= t && size(ev.availability_profile, 2) >= ni ?
                ev.availability_profile[t, ni] : 1.0
            v2g_cap = ev.max_discharge_power_kw * ev.num_vehicles[ni] / 1000.0 * avail
            @constraint(model, vars.ev_v2g[ni, t] <= v2g_cap,
                base_name = "ev_max_v2g_n$(ni)_$(t)")

            # SOC bounds
            total_capacity_mwh = ev.battery_capacity_kwh * ev.num_vehicles[ni] / 1000.0
            @constraint(model, vars.ev_soc[ni, t+1] >= ev.min_soc * total_capacity_mwh,
                base_name = "ev_soc_min_n$(ni)_$(t)")
            @constraint(model, vars.ev_soc[ni, t+1] <= ev.max_soc * total_capacity_mwh,
                base_name = "ev_soc_max_n$(ni)_$(t)")

            # Charge/V2G mutual exclusivity (Big-M relaxation)
            if vars.ev_charge_status !== nothing
                M_ev = max_charge + v2g_cap
                @constraint(model, vars.ev_charging[ni, t] <= M_ev * vars.ev_charge_status[ni, t],
                    base_name = "ev_mutex_charge_n$(ni)_$(t)")
                @constraint(model, vars.ev_v2g[ni, t] <= M_ev * (1 - vars.ev_charge_status[ni, t]),
                    base_name = "ev_mutex_v2g_n$(ni)_$(t)")
            end
        end

        # Cyclic SOC constraint: EV fleet must return to initial SOC at end of window
        @constraint(model, vars.ev_soc[ni, hours+1] == vars.ev_soc[ni, 1],
            base_name = "ev_soc_cyclic_n$(ni)")
    end
end

"""
    recover_uc_duals!(model, vars::PowerSystemVariables, input::PowerSystemInput) -> Bool

Re-extract dual prices from a UC (MIP) solution via fix-and-resolve.

Background: MIPs have no duals by definition, so when a Unit Commitment
model converges, ``has_duals(model) == false`` and the balance-constraint
duals — the locational marginal prices the electricity market cares about
— are unavailable. The standard industry technique is:

1. Round the optimal ``gen_status`` values to ``{0, 1}``.
2. Relax the binary variables to continuous (``unset_binary``) and
   ``fix`` them to those rounded values.
3. Re-solve. The model is now an LP whose objective matches the MIP's
   incumbent, and balance-constraint duals are well-defined.

The function mutates ``model`` in place (the binaries stay unset and
fixed afterwards); call this only after the MIP solve has converged
and right before ``extract_solution``.

Returns ``true`` if duals are now available, ``false`` otherwise (e.g.
the LP re-solve failed, the model wasn't UC, or there were no values
to fix). On ``false``, ``extract_solution`` will produce ``prices = 0``
as before — no silent corruption.
"""
function recover_uc_duals!(model, vars::PowerSystemVariables, input::PowerSystemInput)
    # Not UC → nothing to do; the LP path already exports duals.
    if vars.gen_status === nothing
        return has_duals(model)
    end
    if !has_values(model)
        return false
    end
    # Already have duals (rare in MIP, but skip the work just in case).
    if has_duals(model)
        return true
    end

    n_gen = length(input.generators)
    hours = input.temporal.hours

    # Relax the binary commitment variables to continuous and pin them to
    # the MIP optimum. ``round`` removes any tiny solver floating-point
    # noise that would otherwise make the fix infeasible.
    n_fixed = 0
    for g in 1:n_gen
        for b in vars.buses_of_gen[g], t in 1:hours
            v = vars.gen_status[g, b, t]
            if is_binary(v)
                unset_binary(v)
            end
            val = round(value(v))
            fix(v, val; force=true)
            n_fixed += 1
        end
    end

    # gen_startup is Continuous [0,1] in UC mode (see build_variables!),
    # so its dual is well-defined once gen_status is fixed — no need to
    # fix it.

    # Re-solve as LP. If the solver refuses (rare; can happen when the
    # configured solver doesn't support warm-starts from a MIP basis),
    # the caller sees ``false`` and prices stay zero.
    try
        optimize!(model)
    catch e
        @warn "UC dual recovery: LP re-solve threw an exception" exception=e
        return false
    end

    if !has_duals(model)
        @warn "UC dual recovery: LP re-solve completed but duals are still missing"
        return false
    end

    @info "UC dual recovery: fixed $n_fixed commitment vars and recovered LP duals"
    return true
end

"""
    recover_uc_duals_via_copy(model, vars::PowerSystemVariables, input::PowerSystemInput) -> Union{Matrix{Float64}, Nothing}

Recover locational marginal prices from a UC (MIP) solution without
mutating the original model.

The in-place ``recover_uc_duals!`` variant left JuMP in an inconsistent
state when downstream callers ran ``extract_solution`` on the same
model (``OptimizeNotCalled`` from MOI's solve_time attribute). This
variant takes a fresh ``copy_model`` of the MIP, attaches a clean
optimizer, fixes ``gen_status`` to the rounded MIP values on the copy,
solves it as an LP, and reads the balance-constraint duals there.

Returns the recovered price matrix ``[n_bus × hours]`` in USD/MWh, or
``nothing`` if recovery isn't applicable (model isn't UC, has no
solution, or the LP re-solve failed). The caller is responsible for
overriding ``result.prices`` with the returned matrix.

Cost: ~1× MIP memory during the LP solve (the copy lives only inside
this call), plus the time of the LP itself — typically a fraction of
the MIP solve since the integers are fixed.
"""
function recover_uc_duals_via_copy(
    model, vars::PowerSystemVariables, input::PowerSystemInput,
)
    if vars.gen_status === nothing
        # Not UC — the original LP path already populated prices.
        return nothing
    end
    if !has_values(model)
        return nothing
    end

    n_gen = length(input.generators)
    n_bus = input.network.num_buses
    hours = input.temporal.hours

    # Snapshot the optimal commitment values from the MIP BEFORE the
    # copy so we read from the still-solved original. Rounding scrubs
    # solver floating-point noise (``0.0 ± 1e-9``) that would otherwise
    # make ``fix`` reject the value as infeasible.
    status_vals = Array{Float64}(undef, n_gen, n_bus, hours)
    fill!(status_vals, 0.0)
    for g in 1:n_gen
        for b in vars.buses_of_gen[g], t in 1:hours
            status_vals[g, b, t] = round(value(vars.gen_status[g, b, t]))
        end
    end

    # Clone the model + attach a fresh optimizer built with the same
    # config the MIP used. ``copy_model`` returns a ``ReferenceMap`` we
    # use to translate the original variable / constraint handles into
    # their copies — both ``gen_status`` (for fix) and
    # ``balance_constraints`` (for dual lookup) live on the original.
    new_model, reference_map = try
        copy_model(model)
    catch e
        @warn "UC dual recovery (copy): copy_model failed" exception=e
        return nothing
    end

    optimizer = try
        create_optimizer(
            solver_name=input.solver_name,
            threads=input.threads,
            time_limit=input.time_limit,
            gap=input.gap,
            verbose=false,
            solver_options=input.solver_options,
        )
    catch e
        @warn "UC dual recovery (copy): create_optimizer failed" exception=e
        return nothing
    end
    set_optimizer(new_model, optimizer)

    # On the copy: relax ``gen_status`` to continuous and pin to MIP
    # incumbent values. The original model is untouched.
    for g in 1:n_gen
        for b in vars.buses_of_gen[g], t in 1:hours
            new_v = reference_map[vars.gen_status[g, b, t]]
            if is_binary(new_v)
                unset_binary(new_v)
            end
            fix(new_v, status_vals[g, b, t]; force=true)
        end
    end

    try
        optimize!(new_model)
    catch e
        @warn "UC dual recovery (copy): LP re-solve threw" exception=e
        return nothing
    end

    if !has_duals(new_model)
        @warn "UC dual recovery (copy): LP duals not available after re-solve"
        return nothing
    end

    # Extract balance-constraint duals → $/MWh. The objective is
    # scaled by ``temporal_resolution_hours``, so raw duals need to be
    # divided by ``tres`` to land in real prices (same convention as
    # the original LP path in ``extract_solution``).
    prices = zeros(n_bus, hours)
    tres_norm = max(1.0, Float64(input.temporal.resolution_hours))
    if vars.balance_constraints !== nothing
        for ((b, t), old_con) in vars.balance_constraints
            try
                new_con = reference_map[old_con]
                prices[b, t] = dual(new_con) / tres_norm
            catch
                prices[b, t] = 0.0
            end
        end
    end

    @info "UC dual recovery (copy): recovered LMPs for $(n_bus)×$(hours) cells"
    return prices
end

"""
    extract_solution(model, vars::PowerSystemVariables, input::PowerSystemInput) -> PowerSystemResult

Extract solution values from solved model.

Returns a PowerSystemResult containing all solution values, metrics, and optional
dual prices (if the model is LP or has been fixed and resolved as LP).
"""
function extract_solution(model, vars::PowerSystemVariables, input::PowerSystemInput)
    status = termination_status(model)
    obj = has_values(model) ? objective_value(model) : NaN
    solve_t = solve_time(model)

    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_bus = input.network.num_buses
    n_node = input.network.num_nodes
    b2n = input.network.bus_to_node
    hours = input.temporal.hours

    # Extract values (with default for infeasible)
    # SparseAxisArray variables must be manually converted to dense arrays
    if has_values(model)
        # Generator output — sparse to dense
        gen_out = zeros(n_gen, n_bus, hours)
        for g in 1:n_gen, b in vars.buses_of_gen[g], t in 1:hours
            gen_out[g, b, t] = value(vars.gen_output[g, b, t])
        end

        # Generator status — sparse to dense
        gen_stat = nothing
        if vars.gen_status !== nothing
            gen_stat = zeros(n_gen, n_bus, hours)
            for g in 1:n_gen, b in vars.buses_of_gen[g], t in 1:hours
                gen_stat[g, b, t] = value(vars.gen_status[g, b, t])
            end
        end

        # Generator startup — sparse to dense
        gen_start = nothing
        if vars.gen_startup !== nothing
            gen_start = zeros(n_gen, n_bus, hours)
            for g in 1:n_gen, b in vars.buses_of_gen[g], t in 1:hours
                gen_start[g, b, t] = value(vars.gen_startup[g, b, t])
            end
        end

        # Generator shutdown — sparse to dense (currently always nothing)
        gen_shut = nothing
        if vars.gen_shutdown !== nothing
            gen_shut = zeros(n_gen, n_bus, hours)
            for g in 1:n_gen, b in vars.buses_of_gen[g], t in 1:hours
                gen_shut[g, b, t] = value(vars.gen_shutdown[g, b, t])
            end
        end

        # Expand node-level results to bus-level for backward compatibility.
        # DISTRIBUTE (not copy) the node value uniformly across the node's
        # buses, so that summing buses back to the node — done both by
        # `total_curt = sum(curt)` below and by the Python
        # `_aggregate_buses_to_nodes(..., "sum")` — recovers the original node
        # value. A plain copy inflates the total by the number of buses per
        # node (e.g. ~40× for Cuba's 398-bus / 10-node network).
        buses_per_node = zeros(Int, n_node)
        for b in 1:n_bus
            buses_per_node[b2n[b]] += 1
        end
        curt_node = value.(vars.curtailment)
        curt = zeros(n_bus, hours)
        for b in 1:n_bus
            curt[b, :] = curt_node[b2n[b], :] ./ max(buses_per_node[b2n[b]], 1)
        end

        # Battery variables — sparse to dense
        if n_bat > 0
            b_charge = zeros(n_bat, n_bus, hours)
            b_discharge = zeros(n_bat, n_bus, hours)
            b_soc = zeros(n_bat, n_bus, hours + 1)
            for bi in 1:n_bat, b in vars.buses_of_bat[bi]
                for t in 1:hours
                    b_charge[bi, b, t] = value(vars.bat_charge[bi, b, t])
                    b_discharge[bi, b, t] = value(vars.bat_discharge[bi, b, t])
                end
                for t in 1:(hours+1)
                    b_soc[bi, b, t] = value(vars.bat_soc[bi, b, t])
                end
            end
        else
            b_charge = zeros(0, n_bus, hours)
            b_discharge = zeros(0, n_bus, hours)
            b_soc = zeros(0, n_bus, hours)
        end

        # load_shed is per-bus natively (B2 refactor); extract directly.
        ls = value.(vars.load_shed)
        # Expand co2, reserves to bus-level (still node-level vars)

        # co2: same distribute-don't-copy treatment as curtailment above —
        # node-level emissions split uniformly across the node's buses so the
        # downstream bus→node sum recovers the true node total (a plain copy
        # would inflate it by buses-per-node).
        co2_node = vars.co2_emissions !== nothing ? value.(vars.co2_emissions) : zeros(n_node, hours)
        co2_em = zeros(n_bus, hours)
        for b in 1:n_bus
            co2_em[b, :] = co2_node[b2n[b], :] ./ max(buses_per_node[b2n[b]], 1)
        end

        v_angle = value.(vars.voltage_angle)

        res_static_node = value.(vars.reserve_static)
        res_dynamic_node = value.(vars.reserve_dynamic)
        res_static_loss_node = value.(vars.reserve_static_loss)
        res_dynamic_loss_node = value.(vars.reserve_dynamic_loss)
        res_static = zeros(n_bus, hours)
        res_dynamic = zeros(n_bus, hours)
        res_static_loss = zeros(n_bus, hours)
        res_dynamic_loss = zeros(n_bus, hours)
        for b in 1:n_bus
            df = input.network.buses[b].demand_fraction
            res_static[b, :] = res_static_node[b2n[b], :] .* df
            res_dynamic[b, :] = res_dynamic_node[b2n[b], :] .* df
            res_static_loss[b, :] = res_static_loss_node[b2n[b], :] .* df
            res_dynamic_loss[b, :] = res_dynamic_loss_node[b2n[b], :] .* df
        end

        # Try to extract dual prices from balance constraints
        # The objective is `temporal_resolution_hours × energy_costs`, so raw duals
        # are scaled by tres.  Divide by tres to get actual $/MWh prices.
        prices = zeros(n_bus, hours)
        tres_norm = max(1.0, Float64(input.temporal.resolution_hours))
        if vars.balance_constraints !== nothing && has_duals(model)
            for ((b, t), con) in vars.balance_constraints
                try
                    prices[b, t] = dual(con) / tres_norm
                catch
                    # Dual not available (MIP or solver limitation)
                    prices[b, t] = 0.0
                end
            end
        end
    else
        gen_out = zeros(n_gen, n_bus, hours)
        gen_stat = nothing
        gen_start = nothing
        gen_shut = nothing
        curt = zeros(n_bus, hours)
        b_charge = zeros(max(1, n_bat), n_bus, hours)
        b_discharge = zeros(max(1, n_bat), n_bus, hours)
        b_soc = zeros(max(1, n_bat), n_bus, hours)
        ls = zeros(n_bus, hours)
        co2_em = zeros(n_bus, hours)
        v_angle = zeros(n_bus, hours)
        res_static = zeros(n_bus, hours)
        res_dynamic = zeros(n_bus, hours)
        res_static_loss = zeros(n_bus, hours)
        res_dynamic_loss = zeros(n_bus, hours)
        prices = zeros(n_bus, hours)
    end

    # Calculate metrics
    total_gen = sum(gen_out)
    total_demand = sum(input.demand)
    total_curt = sum(curt)
    total_ls = sum(ls)

    # Renewable energy penetration
    re_gen = 0.0
    for g in 1:n_gen
        if input.generators[g].type == "Renewable"
            re_gen += sum(gen_out[g, :, :])
        end
    end
    re_pen = total_gen > 0 ? re_gen / total_gen : 0.0

    # CO2 emissions (using fuel_co2 factors if available)
    total_co2 = 0.0
    for g in 1:n_gen
        fuel = input.generators[g].fuel
        if haskey(input.fuel_co2, fuel)
            co2_factor = input.fuel_co2[fuel]
            total_co2 += sum(gen_out[g, :, :]) * co2_factor
        end
    end

    # Power flow values (legacy node-pair dict)
    pf = Dict{Tuple{Int,Int}, Vector{Float64}}()
    if has_values(model)
        for (key, var_vec) in vars.power_flow
            pf[key] = value.(var_vec)
        end
    end

    # Per-line power flow values
    pf_by_line = nothing
    if has_values(model) && vars.power_flow_by_line !== nothing
        pf_by_line = [value.(pf_var) for pf_var in vars.power_flow_by_line]
    end

    # Transfer investment values
    trans_inv = nothing
    if has_values(model) && vars.transfer_investment !== nothing
        trans_inv = Dict{Tuple{Int,Int}, Float64}()
        for (key, var) in vars.transfer_investment
            trans_inv[key] = value(var)
        end
    end

    # Investment decisions — sparse to dense
    gen_inv = nothing
    bat_inv_p = nothing
    bat_inv_c = nothing

    if has_values(model)
        if vars.gen_investment !== nothing
            gen_inv = zeros(n_gen, n_bus)
            for g in 1:n_gen, b in vars.buses_of_gen[g]
                gen_inv[g, b] = value(vars.gen_investment[g, b])
            end
        end
        if vars.bat_investment_power !== nothing
            bat_inv_p = zeros(n_bat, n_bus)
            for bi in 1:n_bat, b in vars.buses_of_bat[bi]
                bat_inv_p[bi, b] = value(vars.bat_investment_power[bi, b])
            end
        end
        if vars.bat_investment_capacity !== nothing
            bat_inv_c = zeros(n_bat, n_bus)
            for bi in 1:n_bat, b in vars.buses_of_bat[bi]
                bat_inv_c[bi, b] = value(vars.bat_investment_capacity[bi, b])
            end
        end
    end

    # Battery spillage
    spillage_out = nothing
    if has_values(model) && vars.bat_spillage !== nothing
        spillage_out = zeros(n_bat, n_bus, hours)
        for bi in 1:n_bat, b in 1:n_bus, t in 1:hours
            if vars.bat_spillage[bi, b, t] !== nothing
                spillage_out[bi, b, t] = value(vars.bat_spillage[bi, b, t])
            end
        end
    end

    # EV variables
    ev_charge_out = nothing
    ev_v2g_out = nothing
    ev_soc_out = nothing
    ev_loss_out = nothing
    if has_values(model) && vars.ev_charging !== nothing
        ev_charge_out = value.(vars.ev_charging)
        ev_v2g_out = value.(vars.ev_v2g)
        # ev_soc has hours+1 columns, take first `hours` for consistency
        ev_soc_full = value.(vars.ev_soc)
        ev_soc_out = ev_soc_full[:, 1:hours]
        ev_loss_out = value.(vars.ev_loss)
    end

    # Loss of inertia
    loi_out = nothing
    if has_values(model) && vars.loss_of_inertia !== nothing
        loi_out = value.(vars.loss_of_inertia)
    end

    # Transfer margin
    tm_out = nothing
    if has_values(model) && vars.transfer_margin !== nothing && !isempty(vars.transfer_margin)
        tm_out = Dict{Tuple{Int,Int}, Vector{Float64}}()
        for (key, var_vec) in vars.transfer_margin
            tm_out[key] = value.(var_vec)
        end
    end

    # Reservoir hydroelectric results — sparse to dense
    res_level_out = nothing
    res_spillage_out = nothing
    res_pump_out = nothing
    res_invest_cap_out = nothing
    if has_values(model) && vars.reservoir_level !== nothing
        res_level_out = zeros(n_gen, n_bus, hours + 1)
        res_spillage_out = zeros(n_gen, n_bus, hours)
        res_pump_out = zeros(n_gen, n_bus, hours)
        for g in 1:n_gen, b in vars.buses_of_gen[g]
            for t in 1:hours
                res_spillage_out[g, b, t] = value(vars.reservoir_spillage[g, b, t])
                res_pump_out[g, b, t] = value(vars.reservoir_pump[g, b, t])
            end
            for t in 1:(hours+1)
                res_level_out[g, b, t] = value(vars.reservoir_level[g, b, t])
            end
        end
        if vars.reservoir_invest_capacity !== nothing
            res_invest_cap_out = value.(vars.reservoir_invest_capacity)
        end
    end

    # ── N-1 security duals extraction ──
    n1_gen_duals = nothing
    n1_trans_duals = nothing
    n1_binding = nothing
    n1_cost = 0.0

    if has_values(model) && has_duals(model) && input.n1_security_enabled
        binding_names = String[]

        # Generation N-1 reserve duals (scopf_n1_gen_reserve_t*)
        if input.n1_generation_enabled
            gen_duals = zeros(hours)
            for t in 1:hours
                cname = "scopf_n1_gen_reserve_t$(t)"
                try
                    con = constraint_by_name(model, cname)
                    if con !== nothing
                        d = dual(con)
                        gen_duals[t] = d
                        if abs(d) > 1e-6 && !(cname in binding_names)
                            push!(binding_names, cname)
                        end
                    end
                catch
                end
            end
            if any(abs.(gen_duals) .> 1e-6)
                n1_gen_duals = gen_duals
            end
        end

        # Transmission SCOPF duals (scopf_line*_outage*_t*_pos/neg)
        if input.n1_transmission_enabled && input.n1_scopf_enabled
            trans_duals = Dict{Tuple{Int,Int,Int}, Vector{Float64}}()
            n_lines = length(input.network.transmission_lines)
            for k in 1:n_lines, l in 1:n_lines
                if l == k
                    continue
                end
                duals_pos = zeros(hours)
                duals_neg = zeros(hours)
                has_any = false
                for t in 1:hours
                    for (suffix, dvec) in [("pos", duals_pos), ("neg", duals_neg)]
                        cname = "scopf_line$(l)_outage$(k)_t$(t)_$(suffix)"
                        try
                            con = constraint_by_name(model, cname)
                            if con !== nothing
                                d = dual(con)
                                dvec[t] = d
                                if abs(d) > 1e-6
                                    has_any = true
                                    key_name = "scopf_line$(l)_outage$(k)"
                                    if !(key_name in binding_names)
                                        push!(binding_names, key_name)
                                    end
                                end
                            end
                        catch
                        end
                    end
                end
                if has_any
                    trans_duals[(l, k, 1)] = duals_pos   # dir=1 for positive
                    trans_duals[(l, k, -1)] = duals_neg  # dir=-1 for negative
                end
            end
            if !isempty(trans_duals)
                n1_trans_duals = trans_duals
            end
        end

        n1_binding = isempty(binding_names) ? nothing : binding_names

        # Estimate N-1 security cost: difference between obj and unconstrained
        # (we don't have the unconstrained value, so set to 0 — computed in Python)
        n1_cost = 0.0
    end

    # ── Extract granular cost breakdown ──
    cost_bd = nothing
    if has_values(model) && haskey(model.ext, :cost_expressions)
        ce = model.ext[:cost_expressions]
        tres = get(model.ext, :temporal_resolution_hours, 1.0)
        _v(sym, is_energy=true) = try
            raw = value(get(ce, sym, AffExpr(0.0)))
            is_energy ? raw * tres : raw
        catch
            0.0
        end
        cost_bd = CostBreakdown(
            _v(:fuel_cost),
            _v(:fixed_om_cost),
            _v(:maintenance_cost),
            _v(:startup_cost, false),
            _v(:battery_maintenance_cost),
            _v(:battery_degradation_cost),
            _v(:load_shedding_cost),
            _v(:curtailment_cost),
            _v(:reserve_static_cost),
            _v(:reserve_dynamic_cost),
            _v(:co2_emission_cost),
            _v(:fre_penetration_cost),
            _v(:inertia_cost),
            _v(:soc_violation_cost),
            _v(:transfer_margin_cost),
            _v(:v2g_compensation),
            _v(:flexible_demand_benefit),
            _v(:investment_cost, false),
            _v(:electrolyzer_cost),
            _v(:converter_cost),
            _v(:spillage_cost),
            _v(:delay_retirement_cost, false),
            _v(:reservoir_spillage_cost),
            _v(:demand_shift_cost),
            _v(:rooftop_curtailment_cost),
            _v(:npv_penalty_cost, false),
            _v(:reservoir_invest_cost, false),
            # PrimaryEnergy sub-costs. NOT scaled by tres — the PE objective
            # is built per-period/per-hour and already in solver units for the window.
            _v(:pe_supply_cost, false),
            _v(:pe_loss_cost, false),
            _v(:pe_excess_cost, false),
            _v(:pe_transport_cost, false),
            _v(:pe_investment_cost, false),
            _v(:pe_coupling_slack_cost, false),
            _v(:pe_electrolyzer_cost, false),
            # N-1 reliability-shortfall: same $/MWh scale as load shed (× tres).
            _v(:n1_security_shortfall_cost),
            obj,
        )
    end

    # ACOPF AC-side outputs: filled only when an acopf_* formulation ran;
    # nothing otherwise so DC runs stay zero-cost.
    vm_out = nothing
    qgen_out = nothing
    if vars.acopf_vars !== nothing
        n_bus = input.network.num_buses
        vm_out, _ = extract_acopf_voltages(vars.acopf_vars, n_bus, hours)
        qgen_out = extract_acopf_reactive_gen(vars.acopf_vars, n_gen, n_bus, hours)
    end

    return PowerSystemResult(
        status, obj, solve_t,
        gen_out, gen_stat, gen_start, gen_shut,
        curt, total_curt,
        b_charge, b_discharge, b_soc,
        pf, pf_by_line, v_angle, vm_out, qgen_out, trans_inv,
        res_static, res_dynamic, res_static_loss, res_dynamic_loss,
        ls, co2_em,
        prices,
        total_gen, total_demand, 0.0,  # losses (computed by DC power flow)
        re_pen, total_co2, total_ls,
        gen_inv, bat_inv_p, bat_inv_c,
        spillage_out,
        ev_charge_out, ev_v2g_out, ev_soc_out, ev_loss_out,
        loi_out,
        tm_out,
        res_level_out, res_spillage_out, res_pump_out, res_invest_cap_out,
        n1_gen_duals, n1_trans_duals, n1_binding, n1_cost,
        cost_bd,
    )
end

"""
    calculate_unit_npv(
        rated_capacity, fuel_cost_mwh, fixed_cost_mwh, maintenance_cost_mwh,
        remaining_life, degradation_rate, decommissioning_cost,
        discount_rate, actual_cf, system_lcoe
    ) -> Float64

Calculate NPV for a specific unit.
Matches Python legacy `_calculate_unit_npv` (power_system.py lines 622-806).

# Arguments
- `rated_capacity`: Unit capacity (MW)
- `fuel_cost_mwh`: Fuel cost per MWh
- `fixed_cost_mwh`: Fixed cost per MWh
- `maintenance_cost_mwh`: Maintenance cost per MWh
- `remaining_life`: Years remaining
- `degradation_rate`: Annual degradation rate
- `decommissioning_cost`: Decommissioning cost
- `discount_rate`: Discount rate for NPV
- `actual_cf`: Actual capacity factor from optimization
- `system_lcoe`: System electricity price per MWh
"""
function calculate_unit_npv(
    rated_capacity::Float64, fuel_cost_mwh::Float64,
    fixed_cost_mwh::Float64, maintenance_cost_mwh::Float64,
    remaining_life::Float64, degradation_rate::Float64,
    decommissioning_cost::Float64, discount_rate::Float64,
    actual_cf::Float64, system_lcoe::Float64
)::Float64
    if remaining_life <= 0
        return -decommissioning_cost
    end
    if rated_capacity <= 0
        return 0.0
    end

    # Annual generation and costs
    annual_generation = rated_capacity * actual_cf * Float64(HOURS_STD_YEAR)  # MWh/year
    annual_capacity_costs = (maintenance_cost_mwh + fixed_cost_mwh) * annual_generation
    annual_fuel_costs = annual_generation * fuel_cost_mwh
    total_annual_costs = annual_capacity_costs + annual_fuel_costs

    # NPV over remaining life with degradation
    npv = 0.0
    for year in 0:(Int(floor(remaining_life)) - 1)
        degradation_factor = (1.0 - degradation_rate)^year
        year_generation = annual_generation * degradation_factor
        year_costs = total_annual_costs * degradation_factor
        year_revenue = year_generation * system_lcoe
        net_cash_flow = year_revenue - year_costs
        present_value = net_cash_flow / ((1.0 + discount_rate)^year)
        npv += present_value
    end

    # Subtract decommissioning cost at end of life
    if decommissioning_cost > 0 && remaining_life > 0
        decommissioning_pv = decommissioning_cost / ((1.0 + discount_rate)^remaining_life)
        npv -= decommissioning_pv
    end

    return npv
end

"""
    update_npv_from_results!(input::PowerSystemInput, result::PowerSystemResult)
        -> (updated_unit_npv, updated_replacement_needed, updated_bat_unit_npv, updated_bat_replacement_needed)

Update NPV values for all units using actual generation data from optimization results.
Matches Python legacy `update_npv_from_results` (power_system.py lines 808-938).

Should be called after each optimization window to update unit NPV values based on
real operational performance.

Returns updated dictionaries suitable for passing to next optimization window's input.
"""
function update_npv_from_results!(
    input::PowerSystemInput,
    result::PowerSystemResult
)
    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_bus = input.network.num_buses
    hours = input.temporal.hours
    discount_rate = input.discount_rate

    updated_unit_npv = copy(input.unit_npv)
    updated_replacement_needed = copy(input.replacement_needed)
    updated_bat_unit_npv = copy(input.bat_unit_npv)
    updated_bat_replacement_needed = copy(input.bat_replacement_needed)

    # Compute average system electricity price from result energy_prices
    avg_price = 0.0
    price_count = 0
    for b in 1:n_bus, t in 1:hours
        p = result.energy_prices[b, t]
        if p != 0.0
            avg_price += abs(p)
            price_count += 1
        end
    end
    avg_price = price_count > 0 ? avg_price / price_count : 50.0  # Fallback

    # Update generator NPVs
    for g in 1:n_gen
        gen = input.generators[g]
        for b in 1:n_bus
            rated = gen.rated_power[b]
            if rated <= 0
                continue
            end
            lifetime = length(gen.life_time) >= b ? gen.life_time[b] : 25.0
            initial_age = length(gen.initial_age) >= b ? gen.initial_age[b] : 0.0
            current_age = initial_age + max(0.0, Float64(input.year - input.base_year))
            current_age = min(current_age, lifetime)
            remaining_life = max(0.0, lifetime - current_age)

            degradation_rate = length(gen.degradation_rate) >= b ? gen.degradation_rate[b] : 0.04

            # Compute actual capacity factor from result
            total_gen = 0.0
            total_revenue = 0.0
            for t in 1:hours
                val = result.gen_output[g, b, t]
                if val > 0
                    total_gen += val
                    # Use nodal price if available
                    price_t = abs(result.energy_prices[b, t])
                    if price_t == 0
                        price_t = avg_price
                    end
                    total_revenue += val * price_t
                end
            end
            actual_cf = hours > 0 && rated > 0 ? min(1.0, total_gen / (rated * hours)) : 0.0
            system_lcoe = total_gen > 0 ? total_revenue / total_gen : avg_price

            # Ensure minimum reasonable price
            if system_lcoe < 50.0
                fuel_cost = length(gen.fuel_cost) >= b ? gen.fuel_cost[b] : 0.0
                fixed_cost = length(gen.fixed_cost) >= b ? gen.fixed_cost[b] : 0.0
                maint_cost = length(gen.maintenance_cost) >= b ? gen.maintenance_cost[b] : 0.0
                system_lcoe = max(50.0, (fuel_cost + fixed_cost + maint_cost) * 1.2)
            end

            decom_cost = get(input.decommissioning_cost_gen, (g, b), 0.0)
            fuel_cost = length(gen.fuel_cost) >= b ? gen.fuel_cost[b] : 0.0
            fixed_cost = length(gen.fixed_cost) >= b ? gen.fixed_cost[b] : 0.0
            maint_cost = length(gen.maintenance_cost) >= b ? gen.maintenance_cost[b] : 0.0

            npv = calculate_unit_npv(
                rated, fuel_cost, fixed_cost, maint_cost,
                remaining_life, degradation_rate, decom_cost,
                discount_rate, actual_cf, system_lcoe
            )

            updated_unit_npv[(g, b)] = npv
            updated_replacement_needed[(g, b)] = (
                remaining_life <= 2.0 ||
                npv < input.force_replacement_threshold ||
                (npv < 0 && remaining_life < lifetime / 2)
            )
        end
    end

    # Update battery NPVs
    for bi in 1:n_bat
        bat = input.batteries[bi]
        for b in 1:n_bus
            rated = bat.max_discharge_power[b]
            if rated <= 0
                continue
            end
            lifetime = length(bat.life_time) >= b ? bat.life_time[b] : 15.0
            initial_age = length(bat.initial_age) >= b ? bat.initial_age[b] : 0.0
            current_age = initial_age + max(0.0, Float64(input.year - input.base_year))
            current_age = min(current_age, lifetime)
            remaining_life = max(0.0, lifetime - current_age)

            degradation_rate = length(bat.degradation_rate) >= b ? bat.degradation_rate[b] : 0.05

            # Compute actual capacity factor from discharge
            total_discharge = 0.0
            total_revenue = 0.0
            for t in 1:hours
                val = result.bat_discharge[bi, b, t]
                if val > 0
                    total_discharge += val
                    price_t = abs(result.energy_prices[b, t])
                    if price_t == 0
                        price_t = avg_price
                    end
                    total_revenue += val * price_t
                end
            end
            actual_cf = hours > 0 && rated > 0 ? min(1.0, total_discharge / (rated * hours)) : 0.0
            system_lcoe = total_discharge > 0 ? total_revenue / total_discharge : avg_price

            decom_cost = get(input.decommissioning_cost_bat, (bi, b), 0.0)
            maint_cost = length(bat.maintenance_cost) >= b ? bat.maintenance_cost[b] : 0.0

            npv = calculate_unit_npv(
                rated, 0.0, 0.0, maint_cost,
                remaining_life, degradation_rate, decom_cost,
                discount_rate, actual_cf, system_lcoe
            )

            updated_bat_unit_npv[(bi, b)] = npv
            updated_bat_replacement_needed[(bi, b)] = (
                remaining_life <= 2.0 ||
                npv < input.force_replacement_threshold ||
                (npv < 0 && remaining_life < lifetime / 2)
            )
        end
    end

    return (updated_unit_npv, updated_replacement_needed,
            updated_bat_unit_npv, updated_bat_replacement_needed)
end
