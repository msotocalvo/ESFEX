"""
primary_energy.jl - Primary Energy Supply Chain Model

Models primary energy flows (fuels) with multi-scale temporal resolution
and integration with the electrical system, considering non-electric demands.

Temporal Scales:
- Investment: Capacity decisions (typically annual)
- Primary Period: Fuel supply/transport planning (configurable)
- Hourly: Operational fuel dispatch for power generation

Key Components:
- Fuel supply and import
- Fuel transport between nodes
- Fuel storage dynamics

Constants:
- MW_TO_KW: MW → kW conversion for H2 production equation
- Non-electric demand satisfaction
- Coupling with power system generators
"""

using JuMP: @variable, @constraint, @expression, @objective, Model, AffExpr
using JuMP: value, VariableRef, add_to_expression!, lower_bound, upper_bound

# MW → kW conversion factor for H2 production equation
# H2[kg/hr] = Power[MW] × MW_TO_KW × eff / energy_per_kg
const MW_TO_KW = 1000.0

# =============================================================================
# Temporal Period Mapping
# =============================================================================

"""
    TemporalMapping

Internal struct to hold temporal period mappings.
"""
struct TemporalMapping
    # Number of periods at each scale
    num_primary_periods::Int
    num_investment_periods::Int

    # Period indices
    primary_period_indices::Vector{Int}
    investment_period_indices::Vector{Int}

    # Mappings
    hour_to_primary_period::Dict{Int, Int}
    primary_to_investment_period::Dict{Int, Int}
    hours_in_primary_period::Dict{Int, Vector{Int}}
    primary_periods_in_investment_period::Dict{Int, Vector{Int}}
end

"""
    create_temporal_mapping(hours, primary_resolution, investment_resolution)

Create temporal period mappings for multi-scale optimization.
"""
function create_temporal_mapping(
    hours::Int,
    primary_resolution::Int,
    investment_resolution::Int
)::TemporalMapping
    # Primary periods
    period_length = min(primary_resolution, hours)
    if period_length <= 0
        period_length = hours
    end

    num_primary_periods = cld(hours, period_length)  # Ceiling division

    primary_period_indices = collect(1:num_primary_periods)
    hour_to_primary_period = Dict{Int, Int}()
    hours_in_primary_period = Dict{Int, Vector{Int}}()

    current_hour = 1
    for p in 1:num_primary_periods
        start_h = current_hour
        end_h = min(hours, start_h + period_length - 1)
        hours_in_primary_period[p] = collect(start_h:end_h)
        for h in start_h:end_h
            hour_to_primary_period[h] = p
        end
        current_hour = end_h + 1
    end

    # Investment periods (typically spans entire simulation)
    inv_period_length = min(investment_resolution, hours)
    if inv_period_length <= 0
        inv_period_length = hours
    end

    num_investment_periods = cld(hours, inv_period_length)
    investment_period_indices = collect(1:num_investment_periods)

    primary_to_investment_period = Dict{Int, Int}()
    primary_periods_in_investment_period = Dict{Int, Vector{Int}}()

    for inv_p in 1:num_investment_periods
        primary_periods_in_investment_period[inv_p] = Int[]
        start_hour_inv = (inv_p - 1) * inv_period_length + 1
        end_hour_inv = min(hours, inv_p * inv_period_length)

        for p in primary_period_indices
            if !isempty(hours_in_primary_period[p])
                first_hour_p = hours_in_primary_period[p][1]
                if first_hour_p >= start_hour_inv && first_hour_p <= end_hour_inv
                    primary_to_investment_period[p] = inv_p
                    push!(primary_periods_in_investment_period[inv_p], p)
                end
            end
        end
    end

    return TemporalMapping(
        num_primary_periods,
        num_investment_periods,
        primary_period_indices,
        investment_period_indices,
        hour_to_primary_period,
        primary_to_investment_period,
        hours_in_primary_period,
        primary_periods_in_investment_period
    )
end

# =============================================================================
# Input Data Preparation
# =============================================================================

"""
    prepare_fuel_data(input, temporal)

Prepare fuel supply limits and demand requirements per period.
"""
function prepare_fuel_data(input::PrimaryEnergyInput, temporal::TemporalMapping)
    years_diff = input.year - input.base_year

    # Adjusted fuel prices
    adjusted_prices = Dict{String, Float64}()
    for fuel in input.fuels
        price = fuel.price_base * (1 + fuel.price_growth_rate)^years_diff
        adjusted_prices[fuel.name] = price
    end

    # Max supply per period (scaled from annual availability)
    # CRITICAL FIX: Always use full-year hours as scaling base for annual availability
    # The max_availability in config is specified as "tons/year" (annual availability)
    # In rolling horizon, each window covers a portion of the year, so availability must be
    # prorated based on the actual fraction of the year covered, not the window duration.
    # Using window duration (total_operational_hours) would allow each window to access
    # 100% of annual availability, leading to over-consumption across multiple windows.
    scaling_base_hours = Float64(hours_in_year(input.year))  # Full year hours (8760 or 8784 for leap)
    max_supply_periodic = Dict{String, Dict{Int, Dict{Int, Float64}}}()
    for fuel in input.fuels
        max_supply_periodic[fuel.name] = Dict{Int, Dict{Int, Float64}}()
        for n in 1:input.num_nodes
            max_supply_periodic[fuel.name][n] = Dict{Int, Float64}()
            annual_avail = fuel.max_availability[n]

            for p in temporal.primary_period_indices
                hours_in_p = length(temporal.hours_in_primary_period[p])
                # Scale availability proportionally to this period's duration relative to full year
                fraction = hours_in_p / scaling_base_hours
                max_supply_periodic[fuel.name][n][p] = annual_avail * fraction
            end
        end
    end

    # Non-electric demand per period
    ne_demand_periodic = Dict{Tuple{String, String}, Dict{Int, Dict{Int, Float64}}}()
    for ne_config in input.non_electric_demand
        key = (ne_config.fuel, ne_config.sector)
        ne_demand_periodic[key] = Dict{Int, Dict{Int, Float64}}()

        growth_factor = (1 + ne_config.growth_rate)^years_diff

        for n in 1:input.num_nodes
            ne_demand_periodic[key][n] = Dict{Int, Float64}()
            annual_demand = ne_config.annual_demand[n] * growth_factor

            if annual_demand > 1e-6
                for p in temporal.primary_period_indices
                    hours_in_p = temporal.hours_in_primary_period[p]
                    if isempty(hours_in_p)
                        continue
                    end

                    # Get seasonal factor based on average hour's month
                    # Match Python legacy formula (0-indexed → 1-indexed):
                    #   month_idx_for_p = min(11, avg_hour // int(round(total_hours/12)))
                    avg_hour = round(Int, mean(hours_in_p))
                    hours_per_month = max(1, round(Int, input.hours / 12))
                    month_idx = min(12, div(avg_hour - 1, hours_per_month) + 1)
                    seasonal_factor = ne_config.seasonal_factors[month_idx]

                    # Scale demand to this period
                    fraction_of_year = length(hours_in_p) / Float64(hours_in_year(input.year))
                    ne_demand_periodic[key][n][p] = annual_demand * seasonal_factor * fraction_of_year
                end
            end
        end
    end

    return adjusted_prices, max_supply_periodic, ne_demand_periodic
end

# =============================================================================
# Variable Creation
# =============================================================================

"""
    build_primary_energy_variables!(model, input, temporal)

Create all primary energy decision variables.
"""
function build_primary_energy_variables!(
    model::Model,
    input::PrimaryEnergyInput,
    temporal::TemporalMapping
)::PrimaryEnergyVariables
    n_nodes = input.num_nodes
    n_hours = input.hours
    n_primary = temporal.num_primary_periods
    n_invest = temporal.num_investment_periods

    fuel_names = [f.name for f in input.fuels]
    n_routes = length(input.transport_routes)

    # Collect sectors
    sectors = Set{String}()
    for ne in input.non_electric_demand
        push!(sectors, ne.sector)
    end

    # =========================================================================
    # Investment Variables
    # Skip creation if investment_from_master flag is true (handled by MasterProblem)
    # Matches Python legacy lines 361-379
    # =========================================================================

    transport_inv = Dict{String, Matrix{VariableRef}}()
    storage_inv = Dict{String, Matrix{VariableRef}}()

    # Only create investment variables if NOT handled by MasterProblem
    if input.mode in ["development", "economic_dispatch"] && !input.investment_from_master
        for fuel_name in fuel_names
            if haskey(input.infrastructure, fuel_name)
                infra = input.infrastructure[fuel_name]

                # Transport investment [inv_period, route]
                if n_routes > 0
                    transport_inv[fuel_name] = @variable(model,
                        [inv_p=1:n_invest, r=1:n_routes],
                        lower_bound = 0,
                        base_name = "inv_transport_$(fuel_name)"
                    )
                end

                # Storage investment [inv_period, node]
                storage_inv[fuel_name] = @variable(model,
                    [inv_p=1:n_invest, n=1:n_nodes],
                    lower_bound = 0,
                    base_name = "inv_storage_$(fuel_name)"
                )
            end
        end
    end

    # =========================================================================
    # Primary Period Variables
    # =========================================================================

    fuel_supply_pp = Dict{String, Matrix{VariableRef}}()
    fuel_transport_pp = Dict{String, Matrix{VariableRef}}()
    ne_consumption_pp = Dict{Tuple{String, String}, Matrix{VariableRef}}()
    storage_start_pp = Dict{String, Matrix{VariableRef}}()
    storage_end_pp = Dict{String, Matrix{VariableRef}}()
    loss_supply_pp = Dict{String, Matrix{VariableRef}}()
    excess_supply_pp = Dict{String, Matrix{VariableRef}}()
    net_storage_change_pp = Dict{String, Matrix{VariableRef}}()

    for fuel_name in fuel_names
        # Fuel supply [node, period]
        fuel_supply_pp[fuel_name] = @variable(model,
            [n=1:n_nodes, p=1:n_primary],
            lower_bound = 0,
            base_name = "pp_supply_$(fuel_name)"
        )

        # Fuel transport [route, period]
        if n_routes > 0
            fuel_transport_pp[fuel_name] = @variable(model,
                [r=1:n_routes, p=1:n_primary],
                lower_bound = 0,
                base_name = "pp_transport_$(fuel_name)"
            )
        end

        # Storage levels [node, period]
        storage_start_pp[fuel_name] = @variable(model,
            [n=1:n_nodes, p=1:n_primary],
            lower_bound = 0,
            base_name = "pp_storage_start_$(fuel_name)"
        )

        storage_end_pp[fuel_name] = @variable(model,
            [n=1:n_nodes, p=1:n_primary],
            lower_bound = 0,
            base_name = "pp_storage_end_$(fuel_name)"
        )

        # Loss of supply [node, period]
        loss_supply_pp[fuel_name] = @variable(model,
            [n=1:n_nodes, p=1:n_primary],
            lower_bound = 0,
            base_name = "pp_loss_supply_$(fuel_name)"
        )

        # Excess supply [node, period] - Enabled to match Python legacy
        # Slack variable to allow exceeding max_supply_periodic if needed for feasibility
        # This allows importing more fuel than the periodic limit when necessary (with high cost penalty)
        excess_supply_pp[fuel_name] = @variable(model,
            [n=1:n_nodes, p=1:n_primary],
            lower_bound = 0,
            base_name = "pp_excess_supply_$(fuel_name)"
        )

        # Net hourly storage change [node, period]
        net_storage_change_pp[fuel_name] = @variable(model,
            [n=1:n_nodes, p=1:n_primary],
            base_name = "pp_net_storage_change_$(fuel_name)"
        )

        # Non-electric consumption per sector
        for sector in sectors
            key = (fuel_name, sector)
            ne_consumption_pp[key] = @variable(model,
                [n=1:n_nodes, p=1:n_primary],
                lower_bound = 0,
                base_name = "pp_ne_$(fuel_name)_$(sector)"
            )
        end
    end

    # =========================================================================
    # Hourly Operational Variables
    # =========================================================================

    storage_level_hr = Dict{String, Matrix{VariableRef}}()
    storage_in_hr = Dict{String, Matrix{VariableRef}}()
    storage_out_hr = Dict{String, Matrix{VariableRef}}()
    fuel_for_power_hr = Dict{Int, Matrix{VariableRef}}()
    ne_consumption_hr = Dict{Tuple{String, String}, Matrix{VariableRef}}()
    loss_supply_hr = Dict{String, Matrix{VariableRef}}()
    emissions_hr = Dict{String, Matrix{VariableRef}}()
    coupling_slack_start = Dict{String, Matrix{VariableRef}}()
    coupling_slack_end = Dict{String, Matrix{VariableRef}}()

    for fuel_name in fuel_names
        # Storage level (includes hour T+1 for final state)
        storage_level_hr[fuel_name] = @variable(model,
            [n=1:n_nodes, t=1:(n_hours+1)],
            lower_bound = 0,
            base_name = "hr_storage_$(fuel_name)"
        )

        # Storage in/out [node, hour]
        storage_in_hr[fuel_name] = @variable(model,
            [n=1:n_nodes, t=1:n_hours],
            lower_bound = 0,
            base_name = "hr_storage_in_$(fuel_name)"
        )

        storage_out_hr[fuel_name] = @variable(model,
            [n=1:n_nodes, t=1:n_hours],
            lower_bound = 0,
            base_name = "hr_storage_out_$(fuel_name)"
        )

        # Loss of supply hourly
        loss_supply_hr[fuel_name] = @variable(model,
            [n=1:n_nodes, t=1:n_hours],
            lower_bound = 0,
            base_name = "hr_loss_supply_$(fuel_name)"
        )

        # Emissions from non-electric consumption
        emissions_hr[fuel_name] = @variable(model,
            [n=1:n_nodes, t=1:n_hours],
            lower_bound = 0,
            base_name = "hr_emissions_$(fuel_name)"
        )

        # Coupling slack variables - Enabled to match Python legacy
        # These allow small violations in the coupling between periodic and hourly storage levels
        # when it's structurally impossible to satisfy them (e.g., long windows with insufficient initial storage)
        coupling_slack_start[fuel_name] = @variable(model,
            [n=1:n_nodes, p=1:n_primary],
            lower_bound = 0,
            base_name = "slack_start_$(fuel_name)"
        )

        coupling_slack_end[fuel_name] = @variable(model,
            [n=1:n_nodes, p=1:n_primary],
            lower_bound = 0,
            base_name = "slack_end_$(fuel_name)"
        )

        # Hourly NE consumption per sector
        for sector in sectors
            key = (fuel_name, sector)
            ne_consumption_hr[key] = @variable(model,
                [n=1:n_nodes, t=1:n_hours],
                lower_bound = 0,
                base_name = "hr_ne_$(fuel_name)_$(sector)"
            )
        end
    end

    # Fuel for power generation [gen_idx, node, hour]
    for (gen_idx, fuel_info) in input.generator_fuel_map
        fuel_for_power_hr[gen_idx] = @variable(model,
            [n=1:n_nodes, t=1:n_hours],
            lower_bound = 0,
            base_name = "hr_fuel_power_gen$(gen_idx)"
        )
    end

    # Total primary emissions [node, hour]
    total_emissions_hr = @variable(model,
        [n=1:n_nodes, t=1:n_hours],
        lower_bound = 0,
        base_name = "hr_total_primary_emissions"
    )

    # =========================================================================
    # Electrolyzer Variables (E1: joint H2 production optimization)
    # Matches Python legacy electrolizer_model.py HydrogenProduction class
    # =========================================================================
    elec_power = nothing
    h2_prod = nothing
    elec_invest = nothing

    if input.electrolyzer_config !== nothing
        ec = input.electrolyzer_config

        elec_power = @variable(model,
            [n=1:n_nodes, t=1:n_hours],
            lower_bound = 0,
            base_name = "electrolyzer_power"
        )

        h2_prod = @variable(model,
            [n=1:n_nodes, t=1:n_hours],
            lower_bound = 0,
            base_name = "h2_production"
        )

        elec_invest = @variable(model,
            [n=1:n_nodes],
            lower_bound = 0,
            base_name = "electrolyzer_invest"
        )
        # Set upper bounds on investment
        for n in 1:n_nodes
            set_upper_bound(elec_invest[n], ec.invest_max_power[n])
        end
    end

    return PrimaryEnergyVariables(
        transport_inv,
        storage_inv,
        fuel_supply_pp,
        fuel_transport_pp,
        ne_consumption_pp,
        storage_start_pp,
        storage_end_pp,
        loss_supply_pp,
        excess_supply_pp,
        net_storage_change_pp,
        storage_level_hr,
        storage_in_hr,
        storage_out_hr,
        fuel_for_power_hr,
        ne_consumption_hr,
        loss_supply_hr,
        emissions_hr,
        total_emissions_hr,
        coupling_slack_start,
        coupling_slack_end,
        elec_power,
        h2_prod,
        elec_invest
    )
end

# =============================================================================
# Constraints
# =============================================================================

"""
    add_primary_energy_constraints!(model, vars, input, temporal, adjusted_prices, max_supply, ne_demand)

Add all primary energy constraints to the model.
"""
function add_primary_energy_constraints!(
    model::Model,
    vars::PrimaryEnergyVariables,
    input::PrimaryEnergyInput,
    temporal::TemporalMapping,
    adjusted_prices::Dict{String, Float64},
    max_supply::Dict{String, Dict{Int, Dict{Int, Float64}}},
    ne_demand::Dict{Tuple{String, String}, Dict{Int, Dict{Int, Float64}}}
)
    n_nodes = input.num_nodes
    n_hours = input.hours

    fuel_names = [f.name for f in input.fuels]
    fuel_by_name = Dict(f.name => f for f in input.fuels)

    sectors = Set{String}()
    for ne in input.non_electric_demand
        push!(sectors, ne.sector)
    end

    # =========================================================================
    # I. Investment Constraints
    # =========================================================================

    if input.mode in ["development", "economic_dispatch"]
        for inv_p in temporal.investment_period_indices
            for fuel_name in fuel_names
                if haskey(input.infrastructure, fuel_name)
                    infra = input.infrastructure[fuel_name]
                    fuel = fuel_by_name[fuel_name]

                    # Transport investment limits (route-based)
                    if haskey(vars.transport_capacity_investment, fuel_name)
                        for (r, route) in enumerate(input.transport_routes)
                            if haskey(route.fuel_params, fuel_name)
                                fparams = route.fuel_params[fuel_name]
                                @constraint(model,
                                    vars.transport_capacity_investment[fuel_name][inv_p, r] <=
                                    fparams.capacity * infra.transport_expansion_limit,
                                    base_name = "inv_transport_limit_$(fuel_name)_$(inv_p)_r$(r)"
                                )
                            end
                        end
                    end

                    # Storage investment limits
                    if haskey(vars.storage_capacity_investment, fuel_name)
                        for n in 1:n_nodes
                            base_cap = fuel.storage_capacity[n]
                            @constraint(model,
                                vars.storage_capacity_investment[fuel_name][inv_p, n] <=
                                base_cap * infra.storage_expansion_limit,
                                base_name = "inv_storage_limit_$(fuel_name)_$(inv_p)_$(n)"
                            )
                        end
                    end
                end
            end
        end
    end

    # =========================================================================
    # II. Primary Period Constraints
    # =========================================================================

    for p in temporal.primary_period_indices
        inv_p = get(temporal.primary_to_investment_period, p, 1)

        for fuel_name in fuel_names
            fuel = fuel_by_name[fuel_name]

            for n in 1:n_nodes
                # Periodic supply + transport in = transport out + NE consumption + storage change + loss
                transport_in = AffExpr(0.0)
                transport_out = AffExpr(0.0)

                for (r, route) in enumerate(input.transport_routes)
                    if !haskey(route.fuel_params, fuel_name)
                        continue
                    end
                    fparams = route.fuel_params[fuel_name]

                    if route.to_node == n  # inflow to this node
                        loss_factor = 1.0 - (fparams.transport_losses * route.distance_km / 100.0)
                        add_to_expression!(transport_in,
                            vars.fuel_transport_periodic[fuel_name][r, p], loss_factor)
                    end
                    if route.from_node == n  # outflow from this node
                        add_to_expression!(transport_out,
                            vars.fuel_transport_periodic[fuel_name][r, p])
                    end
                end

                ne_consumption_total = AffExpr(0.0)
                for sector in sectors
                    key = (fuel_name, sector)
                    if haskey(vars.non_electric_consumption_periodic, key)
                        add_to_expression!(ne_consumption_total,
                            vars.non_electric_consumption_periodic[key][n, p])
                    end
                end

                # Balance constraint
                @constraint(model,
                    vars.storage_level_start[fuel_name][n, p] +
                    vars.fuel_supply_periodic[fuel_name][n, p] + transport_in ==
                    vars.storage_level_end[fuel_name][n, p] +
                    ne_consumption_total + transport_out +
                    vars.fuel_loss_of_supply_periodic[fuel_name][n, p] +
                    vars.net_hourly_storage_change[fuel_name][n, p],
                    base_name = "pp_balance_$(fuel_name)_$(n)_$(p)"
                )

                # Supply limit (with excess slack)
                max_sup = get(get(max_supply[fuel_name], n, Dict()), p, Inf)
                @constraint(model,
                    vars.fuel_supply_periodic[fuel_name][n, p] <=
                    max_sup + vars.fuel_excess_supply_periodic[fuel_name][n, p],
                    base_name = "pp_max_supply_$(fuel_name)_$(n)_$(p)"
                )

                # NE demand satisfaction
                for sector in sectors
                    key = (fuel_name, sector)
                    if haskey(ne_demand, key)
                        required = get(get(ne_demand[key], n, Dict()), p, 0.0)
                        if required > 1e-6
                            @constraint(model,
                                vars.non_electric_consumption_periodic[key][n, p] <= required,
                                base_name = "pp_ne_max_$(fuel_name)_$(sector)_$(n)_$(p)"
                            )
                            @constraint(model,
                                vars.non_electric_consumption_periodic[key][n, p] +
                                vars.fuel_loss_of_supply_periodic[fuel_name][n, p] >= required,
                                base_name = "pp_ne_satisfy_$(fuel_name)_$(sector)_$(n)_$(p)"
                            )
                        end
                    end
                end

                # Storage continuity
                if p > 1
                    @constraint(model,
                        vars.storage_level_start[fuel_name][n, p] ==
                        vars.storage_level_end[fuel_name][n, p-1],
                        base_name = "pp_storage_continuity_$(fuel_name)_$(n)_$(p)"
                    )
                else
                    # Initial storage level (p == 1)
                    initial_level = if input.initial_storage_levels !== nothing &&
                                       haskey(input.initial_storage_levels, fuel_name) &&
                                       n <= length(input.initial_storage_levels[fuel_name])
                        input.initial_storage_levels[fuel_name][n]
                    else
                        fuel.initial_storage_level[n] * fuel.storage_capacity[n]
                    end

                    @constraint(model,
                        vars.storage_level_start[fuel_name][n, 1] == initial_level,
                        base_name = "pp_storage_initial_$(fuel_name)_$(n)"
                    )

                    # Fix hourly storage at t=1 too
                    @constraint(model,
                        vars.fuel_storage_level_hourly[fuel_name][n, 1] == initial_level,
                        base_name = "hr_storage_initial_$(fuel_name)_$(n)"
                    )
                end

                # Storage capacity constraints
                base_cap = fuel.storage_capacity[n]
                cumul_cap = get(get(get(input.cumulative_capacities, "storage", Dict()), fuel_name, Dict()), n, 0.0)
                inv_cap = if haskey(vars.storage_capacity_investment, fuel_name)
                    vars.storage_capacity_investment[fuel_name][inv_p, n]
                else
                    0.0
                end
                total_cap = base_cap + cumul_cap + inv_cap

                @constraint(model,
                    vars.storage_level_end[fuel_name][n, p] <= total_cap,
                    base_name = "pp_storage_max_end_$(fuel_name)_$(n)_$(p)"
                )

                @constraint(model,
                    vars.storage_level_start[fuel_name][n, p] <= total_cap,
                    base_name = "pp_storage_max_start_$(fuel_name)_$(n)_$(p)"
                )
            end

            # Transport capacity constraints (route-based)
            for (r, route) in enumerate(input.transport_routes)
                if !haskey(route.fuel_params, fuel_name)
                    continue
                end
                fparams = route.fuel_params[fuel_name]
                base_daily_cap = fparams.capacity

                # Look up cumulative capacity using route index
                cumul_cap = get(get(get(get(input.cumulative_capacities, "transport", Dict()), fuel_name, Dict()), "routes", Dict()), r, 0.0)
                inv_cap = if haskey(vars.transport_capacity_investment, fuel_name)
                    vars.transport_capacity_investment[fuel_name][inv_p, r]
                else
                    0.0
                end

                total_daily_cap = base_daily_cap + cumul_cap + inv_cap
                hours_in_p = length(temporal.hours_in_primary_period[p])
                days_in_p = hours_in_p / 24.0

                @constraint(model,
                    vars.fuel_transport_periodic[fuel_name][r, p] <=
                    total_daily_cap * days_in_p,
                    base_name = "pp_transport_cap_$(fuel_name)_r$(r)_$(p)"
                )
            end
        end
    end

    # =========================================================================
    # III. Hourly Operational Constraints
    # =========================================================================

    for t in 1:n_hours
        p = temporal.hour_to_primary_period[t]

        for fuel_name in fuel_names
            fuel = fuel_by_name[fuel_name]
            storage_eff = if haskey(input.infrastructure, fuel_name)
                input.infrastructure[fuel_name].storage_efficiency
            else
                # @debug not @warn: this check sits inside a
                # (hours × fuels) loop — a missing config would emit
                # tens of thousands of identical messages. The default
                # is fine; the config gap is a setup concern, not a
                # per-iteration runtime fault.
                @debug "Storage efficiency not configured for $fuel_name, using default 0.8"
                0.8
            end

            for n in 1:n_nodes
                # Hourly storage balance
                # Matches Python legacy (primary_energy.py lines 711-716):
                #   level[t+1] = level[t] + (storage_in + h2_production) * efficiency - storage_out
                # For Hydrogen, also add production from electrolyzers
                # E1: Use optimization variable when electrolyzer_config is provided,
                # otherwise fall back to fixed h2_production_hourly data
                if fuel_name == "Hydrogen" && vars.h2_production !== nothing
                    # Joint optimization: H2 production is a variable (units/hr)
                    h2_var = vars.h2_production[n, t] / MW_TO_KW  # Convert to storage units
                    @constraint(model,
                        vars.fuel_storage_level_hourly[fuel_name][n, t+1] ==
                        vars.fuel_storage_level_hourly[fuel_name][n, t] +
                        (vars.fuel_storage_in_hourly[fuel_name][n, t] + h2_var) * storage_eff -
                        vars.fuel_storage_out_hourly[fuel_name][n, t],
                        base_name = "hr_storage_balance_$(fuel_name)_$(n)_$(t)"
                    )
                elseif fuel_name == "Hydrogen" && input.h2_production_hourly !== nothing
                    # Fixed data fallback (legacy behavior)
                    h2_fixed = input.h2_production_hourly[t, n] / MW_TO_KW
                    @constraint(model,
                        vars.fuel_storage_level_hourly[fuel_name][n, t+1] ==
                        vars.fuel_storage_level_hourly[fuel_name][n, t] +
                        (vars.fuel_storage_in_hourly[fuel_name][n, t] + h2_fixed) * storage_eff -
                        vars.fuel_storage_out_hourly[fuel_name][n, t],
                        base_name = "hr_storage_balance_$(fuel_name)_$(n)_$(t)"
                    )
                else
                    @constraint(model,
                        vars.fuel_storage_level_hourly[fuel_name][n, t+1] ==
                        vars.fuel_storage_level_hourly[fuel_name][n, t] +
                        vars.fuel_storage_in_hourly[fuel_name][n, t] * storage_eff -
                        vars.fuel_storage_out_hourly[fuel_name][n, t],
                        base_name = "hr_storage_balance_$(fuel_name)_$(n)_$(t)"
                    )
                end

                # Cannot dispatch more than available
                @constraint(model,
                    vars.fuel_storage_out_hourly[fuel_name][n, t] <=
                    vars.fuel_storage_level_hourly[fuel_name][n, t],
                    base_name = "hr_storage_max_out_$(fuel_name)_$(n)_$(t)"
                )

                # Max hourly dispatch rate (fraction of total storage capacity)
                # Matches Python legacy (primary_energy.py lines 724-731)
                if haskey(input.infrastructure, fuel_name)
                    infra = input.infrastructure[fuel_name]
                    if infra.max_hourly_dispatch_rate >= 0
                        base_cap = fuel.storage_capacity[n]
                        cumul_cap = get(get(get(input.cumulative_capacities, "storage", Dict()), fuel_name, Dict()), n, 0.0)
                        inv_p = get(temporal.primary_to_investment_period, p, 1)
                        inv_cap = if haskey(vars.storage_capacity_investment, fuel_name)
                            vars.storage_capacity_investment[fuel_name][inv_p, n]
                        else
                            0.0
                        end
                        total_cap = base_cap + cumul_cap + inv_cap
                        @constraint(model,
                            vars.fuel_storage_out_hourly[fuel_name][n, t] <=
                            infra.max_hourly_dispatch_rate * total_cap,
                            base_name = "hr_storage_dispatch_rate_$(fuel_name)_$(n)_$(t)"
                        )
                    end
                end

                # Minimum storage level
                if fuel.min_storage_level > 0
                    base_cap = fuel.storage_capacity[n]
                    if base_cap > 0
                        @constraint(model,
                            vars.fuel_storage_level_hourly[fuel_name][n, t] >=
                            fuel.min_storage_level * base_cap,
                            base_name = "hr_storage_min_$(fuel_name)_$(n)_$(t)"
                        )
                    end
                end

                # Maximum storage level
                base_cap = fuel.storage_capacity[n]
                cumul_cap = get(get(get(input.cumulative_capacities, "storage", Dict()), fuel_name, Dict()), n, 0.0)
                if base_cap > 0 || cumul_cap > 0
                    inv_p = get(temporal.primary_to_investment_period, p, 1)
                    inv_cap = if haskey(vars.storage_capacity_investment, fuel_name)
                        vars.storage_capacity_investment[fuel_name][inv_p, n]
                    else
                        0.0
                    end
                    total_cap = base_cap + cumul_cap + inv_cap

                    @constraint(model,
                        vars.fuel_storage_level_hourly[fuel_name][n, t] <= total_cap,
                        base_name = "hr_storage_max_$(fuel_name)_$(n)_$(t)"
                    )
                end

                # Fuel needed for power + NE demand <= storage out + loss
                fuel_for_power = AffExpr(0.0)
                for (gen_idx, fuel_info) in input.generator_fuel_map
                    gen_fuel_name, _, _, _ = fuel_info
                    if gen_fuel_name == fuel_name
                        add_to_expression!(fuel_for_power,
                            vars.fuel_for_power_hourly[gen_idx][n, t])
                    end
                end

                ne_hourly = AffExpr(0.0)
                for sector in sectors
                    key = (fuel_name, sector)
                    if haskey(vars.non_electric_consumption_hourly, key)
                        add_to_expression!(ne_hourly,
                            vars.non_electric_consumption_hourly[key][n, t])
                    end
                end

                # Hourly NE demand limit per sector
                # Matches Python legacy (primary_energy.py lines 773-789):
                # Each hour's NE consumption cannot exceed periodic_demand / hours_in_period
                hours_in_p = temporal.hours_in_primary_period[p]
                n_hours_in_p = length(hours_in_p)
                for sector in sectors
                    key = (fuel_name, sector)
                    if haskey(vars.non_electric_consumption_hourly, key) && haskey(ne_demand, key)
                        required_periodic = get(get(ne_demand[key], n, Dict()), p, 0.0)
                        if required_periodic > 1e-6 && n_hours_in_p > 0
                            hourly_limit = required_periodic / n_hours_in_p
                            @constraint(model,
                                vars.non_electric_consumption_hourly[key][n, t] <= hourly_limit,
                                base_name = "hr_ne_limit_$(fuel_name)_$(sector)_$(n)_$(t)"
                            )
                        end
                    end
                end

                @constraint(model,
                    vars.fuel_storage_out_hourly[fuel_name][n, t] +
                    vars.fuel_loss_of_supply_hourly[fuel_name][n, t] >=
                    fuel_for_power + ne_hourly,
                    base_name = "hr_demand_coverage_$(fuel_name)_$(n)_$(t)"
                )

                # Emissions from NE consumption
                # Matches Python legacy (primary_energy.py lines 837-844):
                # Conditional formula based on energy_content
                if fuel.energy_content > 1e-6
                    # If energy_content is defined and non-zero: emissions = consumption × energy_content × emission_factor
                    @constraint(model,
                        vars.primary_sector_emissions_hourly[fuel_name][n, t] ==
                        ne_hourly * fuel.energy_content * fuel.emission_factor,
                        base_name = "hr_emissions_$(fuel_name)_$(n)_$(t)"
                    )
                else
                    # If EF is per physical unit or no energy content: emissions = consumption × emission_factor
                    @constraint(model,
                        vars.primary_sector_emissions_hourly[fuel_name][n, t] ==
                        ne_hourly * fuel.emission_factor,
                        base_name = "hr_emissions_direct_$(fuel_name)_$(n)_$(t)"
                    )
                end
            end
        end

        # Total primary emissions
        for n in 1:n_nodes
            total_em = AffExpr(0.0)
            for fuel_name in fuel_names
                add_to_expression!(total_em,
                    vars.primary_sector_emissions_hourly[fuel_name][n, t])
            end
            @constraint(model,
                vars.total_primary_emissions_hourly[n, t] == total_em,
                base_name = "hr_total_emissions_$(n)_$(t)"
            )
        end
    end

    # =========================================================================
    # IV. Coupling Constraints (Temporal Scale Linking)
    # =========================================================================

    for p in temporal.primary_period_indices
        hours_in_p = temporal.hours_in_primary_period[p]
        if isempty(hours_in_p)
            continue
        end
        first_hour = hours_in_p[1]
        last_hour = hours_in_p[end]

        for fuel_name in fuel_names
            # Storage efficiency for coupling constraint consistency
            storage_eff = if haskey(input.infrastructure, fuel_name)
                input.infrastructure[fuel_name].storage_efficiency
            else
                0.8
            end

            for n in 1:n_nodes
                # Link hourly to periodic at period start
                @constraint(model,
                    vars.fuel_storage_level_hourly[fuel_name][n, first_hour] +
                    vars.coupling_slack_start[fuel_name][n, p] >=
                    vars.storage_level_start[fuel_name][n, p],
                    base_name = "link_storage_start_$(fuel_name)_$(n)_$(p)"
                )

                # Link hourly to periodic at period end
                @constraint(model,
                    vars.fuel_storage_level_hourly[fuel_name][n, last_hour+1] +
                    vars.coupling_slack_end[fuel_name][n, p] >=
                    vars.storage_level_end[fuel_name][n, p],
                    base_name = "link_storage_end_$(fuel_name)_$(n)_$(p)"
                )

                # Link net hourly storage ops to period
                # Must match hourly dynamics: level[t+1] = level[t] + in*eff - out
                # So net change (withdrawal) = out - in*eff
                storage_net_change = AffExpr(0.0)
                for t in hours_in_p
                    add_to_expression!(storage_net_change,
                        vars.fuel_storage_out_hourly[fuel_name][n, t])
                    add_to_expression!(storage_net_change,
                        vars.fuel_storage_in_hourly[fuel_name][n, t], -storage_eff)
                end
                @constraint(model,
                    vars.net_hourly_storage_change[fuel_name][n, p] == storage_net_change,
                    base_name = "link_net_storage_$(fuel_name)_$(n)_$(p)"
                )

                # Link periodic supply to hourly storage_in
                storage_in_sum = AffExpr(0.0)
                for t in hours_in_p
                    add_to_expression!(storage_in_sum,
                        vars.fuel_storage_in_hourly[fuel_name][n, t])
                end
                @constraint(model,
                    storage_in_sum == vars.fuel_supply_periodic[fuel_name][n, p],
                    base_name = "link_supply_storage_in_$(fuel_name)_$(n)_$(p)"
                )
            end
        end

        # Link hourly NE consumption to periodic
        for fuel_name in fuel_names
            for sector in sectors
                key = (fuel_name, sector)
                if haskey(vars.non_electric_consumption_hourly, key) &&
                   haskey(vars.non_electric_consumption_periodic, key)
                    for n in 1:n_nodes
                        hourly_sum = AffExpr(0.0)
                        for t in hours_in_p
                            add_to_expression!(hourly_sum,
                                vars.non_electric_consumption_hourly[key][n, t])
                        end
                        @constraint(model,
                            hourly_sum == vars.non_electric_consumption_periodic[key][n, p],
                            base_name = "link_ne_$(fuel_name)_$(sector)_$(n)_$(p)"
                        )
                    end
                end
            end
        end

        # Minimum storage level at period boundaries
        # Matches Python legacy (primary_energy.py lines 877-893):
        # At end of each period, enforce min storage on both hourly and periodic levels
        for fuel_name in fuel_names
            fuel = fuel_by_name[fuel_name]
            if fuel.min_storage_level > 0
                for n in 1:n_nodes
                    base_cap = fuel.storage_capacity[n]
                    cumul_cap = get(get(get(input.cumulative_capacities, "storage", Dict()), fuel_name, Dict()), n, 0.0)
                    inv_p = get(temporal.primary_to_investment_period, p, 1)
                    inv_cap = if haskey(vars.storage_capacity_investment, fuel_name)
                        vars.storage_capacity_investment[fuel_name][inv_p, n]
                    else
                        0.0
                    end
                    total_cap = base_cap + cumul_cap + inv_cap
                    min_storage_abs = fuel.min_storage_level * total_cap

                    if base_cap > 0 || (isa(cumul_cap, Number) && cumul_cap > 0)
                        # Hourly level at end of period
                        @constraint(model,
                            vars.fuel_storage_level_hourly[fuel_name][n, last_hour + 1] >= min_storage_abs,
                            base_name = "hr_storage_min_final_$(fuel_name)_$(n)_$(p)"
                        )
                        # Periodic end level
                        @constraint(model,
                            vars.storage_level_end[fuel_name][n, p] >= min_storage_abs,
                            base_name = "pp_storage_min_end_$(fuel_name)_$(n)_$(p)"
                        )
                    end
                end
            end
        end

        # Last-period minimum storage in development mode
        # Matches Python legacy (primary_energy.py lines 656-661):
        # Enforce min fill percentage on last period end for sustainability
        if p == temporal.primary_period_indices[end] && input.mode == "development"
            for fuel_name in fuel_names
                if haskey(input.infrastructure, fuel_name)
                    infra = input.infrastructure[fuel_name]
                    fuel = fuel_by_name[fuel_name]
                    for n in 1:n_nodes
                        base_cap = fuel.storage_capacity[n]
                        if base_cap > 0
                            cumul_cap = get(get(get(input.cumulative_capacities, "storage", Dict()), fuel_name, Dict()), n, 0.0)
                            inv_p = get(temporal.primary_to_investment_period, p, 1)
                            inv_cap = if haskey(vars.storage_capacity_investment, fuel_name)
                                vars.storage_capacity_investment[fuel_name][inv_p, n]
                            else
                                0.0
                            end
                            total_cap = base_cap + cumul_cap + inv_cap
                            # Use storage_expansion_limit as proxy for min_level_percentage
                            # (Python uses fuel_infrastructure.storage_facilities.min_level_percentage, default 0.1)
                            min_fill = 0.1
                            @constraint(model,
                                vars.storage_level_end[fuel_name][n, p] >= min_fill * total_cap,
                                base_name = "pp_storage_min_last_$(fuel_name)_$(n)_$(p)"
                            )
                        end
                    end
                end
            end
        end
    end
end

"""
    add_electrolyzer_constraints!(model, vars, input)

Add electrolyzer power, production, and ramp constraints.
Matches Python legacy electrolizer_model.py add_constraints().
"""
function add_electrolyzer_constraints!(
    model::Model,
    vars::PrimaryEnergyVariables,
    input::PrimaryEnergyInput
)
    if vars.electrolyzer_power === nothing || input.electrolyzer_config === nothing
        return
    end

    ec = input.electrolyzer_config
    n_nodes = input.num_nodes
    n_hours = input.hours

    for n in 1:n_nodes
        # Total capacity = existing + invested
        total_cap = ec.rated_power[n] + vars.electrolyzer_investment[n]
        avg_eff = (ec.eff_at_rated[n] + ec.eff_at_min[n]) / 2.0

        for t in 1:n_hours
            # Power limit
            @constraint(model,
                vars.electrolyzer_power[n, t] <= total_cap,
                base_name = "electrolyzer_max_power_$(n)_$(t)"
            )

            # H2 production: H2[kg] = Power[MW] * 1000 * eff / (kWh/kg)
            @constraint(model,
                vars.h2_production[n, t] ==
                vars.electrolyzer_power[n, t] * MW_TO_KW * avg_eff / ec.energy_per_kg_h2,
                base_name = "h2_production_$(n)_$(t)"
            )

            # Ramp constraints
            if t > 1
                @constraint(model,
                    vars.electrolyzer_power[n, t] - vars.electrolyzer_power[n, t-1] <=
                    total_cap * ec.ramp_up[n],
                    base_name = "electrolyzer_ramp_up_$(n)_$(t)"
                )
                @constraint(model,
                    vars.electrolyzer_power[n, t-1] - vars.electrolyzer_power[n, t] <=
                    total_cap * ec.ramp_down[n],
                    base_name = "electrolyzer_ramp_down_$(n)_$(t)"
                )
            end
        end
    end
end

# =============================================================================
# Objective Terms
# =============================================================================

"""
    get_primary_energy_objective_terms(vars, input, temporal, adjusted_prices)

Build a granular decomposition of the primary-energy objective contribution
keyed by category symbol. The caller (`integrate_with_power_system`) sums
the dict's values into the PowerSystem objective AND merges the dict into
`model.ext[:cost_expressions]` so each sub-cost lands in CostBreakdown.

Buckets:
  :pe_investment_cost     — transport + storage infrastructure annualised CAPEX
  :pe_supply_cost         — base_price + import_cost × periodic supply
  :pe_loss_cost           — loss-of-supply penalty (periodic + hourly)
  :pe_excess_cost         — excess-supply penalty
  :pe_transport_cost      — route transport cost × periodic flow
  :pe_coupling_slack_cost — periodic↔hourly storage-level linking slack
  :pe_electrolyzer_cost   — PE-side electrolyzer CAPEX/OPEX (distinct from
                            the PowerSystem-side electrolyzer in `cost_expressions`).
"""
function get_primary_energy_objective_terms(
    vars::PrimaryEnergyVariables,
    input::PrimaryEnergyInput,
    temporal::TemporalMapping,
    adjusted_prices::Dict{String, Float64}
)::Dict{Symbol, AffExpr}
    pe_investment_cost     = AffExpr(0.0)
    pe_supply_cost         = AffExpr(0.0)
    pe_loss_cost           = AffExpr(0.0)
    pe_excess_cost         = AffExpr(0.0)
    pe_transport_cost      = AffExpr(0.0)
    pe_coupling_slack_cost = AffExpr(0.0)
    pe_electrolyzer_cost   = AffExpr(0.0)

    fuel_names = [f.name for f in input.fuels]
    fuel_by_name = Dict(f.name => f for f in input.fuels)
    n_nodes = input.num_nodes

    loss_penalty = input.loss_of_fuel_supply_penalty

    # =========================================================================
    # Investment Costs
    # =========================================================================

    if input.mode in ["development", "economic_dispatch"]
        for inv_p in temporal.investment_period_indices
            for fuel_name in fuel_names
                if haskey(input.infrastructure, fuel_name)
                    infra = input.infrastructure[fuel_name]

                    # Capital recovery factor for transport
                    crf_transport = if infra.lifetime_transport > 0 && input.discount_rate > 0
                        r = input.discount_rate
                        n = infra.lifetime_transport
                        (r * (1 + r)^n) / ((1 + r)^n - 1)
                    else
                        1.0 / max(infra.lifetime_transport, 1.0)
                    end

                    # Capital recovery factor for storage
                    crf_storage = if infra.lifetime_storage > 0 && input.discount_rate > 0
                        r = input.discount_rate
                        n = infra.lifetime_storage
                        (r * (1 + r)^n) / ((1 + r)^n - 1)
                    else
                        1.0 / max(infra.lifetime_storage, 1.0)
                    end

                    # Transport investment cost (route-based)
                    if haskey(vars.transport_capacity_investment, fuel_name)
                        for (r, route) in enumerate(input.transport_routes)
                            add_to_expression!(pe_investment_cost,
                                vars.transport_capacity_investment[fuel_name][inv_p, r],
                                infra.transport_investment_cost * route.distance_km * crf_transport)
                        end
                    end

                    # Storage investment cost
                    if haskey(vars.storage_capacity_investment, fuel_name)
                        for n in 1:n_nodes
                            add_to_expression!(pe_investment_cost,
                                vars.storage_capacity_investment[fuel_name][inv_p, n],
                                infra.storage_investment_cost * crf_storage)
                        end
                    end
                end
            end
        end
    end

    # =========================================================================
    # Operational Costs (Primary Period)
    # =========================================================================

    for p in temporal.primary_period_indices
        for fuel_name in fuel_names
            fuel = fuel_by_name[fuel_name]
            base_price = adjusted_prices[fuel_name]

            for n in 1:n_nodes
                # Supply cost
                import_cost = fuel.import_cost[n]
                add_to_expression!(pe_supply_cost,
                    vars.fuel_supply_periodic[fuel_name][n, p],
                    base_price + import_cost)

                # Loss of supply penalty
                add_to_expression!(pe_loss_cost,
                    vars.fuel_loss_of_supply_periodic[fuel_name][n, p],
                    loss_penalty)

                # Excess supply penalty
                add_to_expression!(pe_excess_cost,
                    vars.fuel_excess_supply_periodic[fuel_name][n, p],
                    loss_penalty)
            end

            # Transport cost (route-based, outside node loop to avoid counting N times)
            for (r, route) in enumerate(input.transport_routes)
                if !haskey(route.fuel_params, fuel_name)
                    continue
                end
                fparams = route.fuel_params[fuel_name]
                add_to_expression!(pe_transport_cost,
                    vars.fuel_transport_periodic[fuel_name][r, p],
                    fparams.transport_cost * route.distance_km)
            end
        end
    end

    # =========================================================================
    # Hourly Loss Penalties
    # =========================================================================

    for fuel_name in fuel_names
        for n in 1:n_nodes
            for t in 1:input.hours
                add_to_expression!(pe_loss_cost,
                    vars.fuel_loss_of_supply_hourly[fuel_name][n, t],
                    loss_penalty)
            end
        end
    end

    # =========================================================================
    # Coupling Slack Penalties (periodic-hourly storage level linking)
    # =========================================================================

    coupling_slack_pen = input.coupling_slack_penalty

    for fuel_name in fuel_names
        for n in 1:n_nodes
            for p in temporal.primary_period_indices
                add_to_expression!(pe_coupling_slack_cost,
                    vars.coupling_slack_start[fuel_name][n, p],
                    coupling_slack_pen)
                add_to_expression!(pe_coupling_slack_cost,
                    vars.coupling_slack_end[fuel_name][n, p],
                    coupling_slack_pen)
            end
        end
    end

    # =========================================================================
    # Electrolyzer Costs (E1: joint H2 production optimization)
    # Matches Python legacy electrolizer_model.py get_objective_terms()
    # =========================================================================
    if vars.electrolyzer_investment !== nothing && input.electrolyzer_config !== nothing
        ec = input.electrolyzer_config
        n_hours = input.hours

        for n in 1:n_nodes
            # Investment cost (annualized)
            if ec.life_time[n] > 0
                hourly_inv_cost = ec.invest_cost[n] / ec.life_time[n] / Float64(HOURS_STD_YEAR)
                add_to_expression!(pe_electrolyzer_cost, vars.electrolyzer_investment[n],
                    hourly_inv_cost * n_hours)
            end

            # Fixed O&M cost: (rated + invested) * fixed_cost * hours
            total_cap = ec.rated_power[n] + vars.electrolyzer_investment[n]
            add_to_expression!(pe_electrolyzer_cost, total_cap, ec.fixed_cost[n] * n_hours)

            # Variable + water costs
            for t in 1:n_hours
                add_to_expression!(pe_electrolyzer_cost, vars.electrolyzer_power[n, t], ec.variable_cost[n])
                add_to_expression!(pe_electrolyzer_cost, vars.h2_production[n, t], ec.water_cost)
            end
        end
    end

    return Dict{Symbol, AffExpr}(
        :pe_investment_cost     => pe_investment_cost,
        :pe_supply_cost         => pe_supply_cost,
        :pe_loss_cost           => pe_loss_cost,
        :pe_excess_cost         => pe_excess_cost,
        :pe_transport_cost      => pe_transport_cost,
        :pe_coupling_slack_cost => pe_coupling_slack_cost,
        :pe_electrolyzer_cost   => pe_electrolyzer_cost,
    )
end

# =============================================================================
# Integration with PowerSystem
# =============================================================================

"""
    couple_primary_energy_to_power_system!(model, pe_vars, ps_vars, input)

Add coupling constraints between primary energy and power system.

The generator output is limited by available fuel supply.
"""
function couple_primary_energy_to_power_system!(
    model::Model,
    pe_vars::PrimaryEnergyVariables,
    ps_vars::PowerSystemVariables,
    input::PrimaryEnergyInput;
    bus_to_node::Vector{Int} = Int[],
    resolution_hours::Float64 = 1.0
)
    NUMERICAL_TOLERANCE = 1e-6
    n_nodes = input.num_nodes
    n_hours = input.hours

    # If bus_to_node is empty, assume identity mapping (bus == node)
    use_b2n = !isempty(bus_to_node)

    n_coupling_constraints = 0

    # @debug not @info: this function runs once per window — under
    # rolling horizon that's 60+ windows × 25 years of identical setup
    # noise. Visible in verbose/debug log levels.
    @debug "PE Coupling: $(length(input.generator_fuel_map)) fuel-mapped generators, " *
          "$(length(ps_vars.buses_of_gen)) PS generators, " *
          "use_b2n=$use_b2n, n_nodes=$n_nodes, n_hours=$n_hours, resolution=$resolution_hours h"

    for (gen_idx, fuel_info) in input.generator_fuel_map
        fuel_name, mwhe_per_unit, _, _ = fuel_info

        # Skip generators with near-zero conversion
        if mwhe_per_unit <= NUMERICAL_TOLERANCE
            continue
        end

        # Skip generators not present in power system
        if gen_idx > length(ps_vars.buses_of_gen)
            # @debug not @info: this fires once per generator inside
            # a coupling loop — N gens × W windows × Y years of noise
            # otherwise. Aggregate stats are reported once below.
            @debug "PE Coupling: gen $gen_idx ($fuel_name) skipped: gen_idx > n_ps_gen ($(length(ps_vars.buses_of_gen)))"
            continue
        end

        buses = ps_vars.buses_of_gen[gen_idx]

        for b in buses
            # Map bus index to node index for PE variables
            node = use_b2n ? bus_to_node[b] : b
            # Clamp to valid PE node range
            if node < 1 || node > n_nodes
                continue
            end

            # Check that fuel_for_power_hourly has this gen_idx
            if !haskey(pe_vars.fuel_for_power_hourly, gen_idx)
                continue
            end

            for t in 1:n_hours
                # gen_output is in MW (power). Each timestep spans resolution_hours.
                # Energy per timestep = gen_output_MW × resolution_hours (MWh).
                # Fuel needed = energy / mwhe_per_unit (tons).
                # fuel_for_power is in tons/timestep.
                # Constraint: gen_output × resolution_hours <= fuel_for_power × mwhe_per_unit
                @constraint(model,
                    ps_vars.gen_output[gen_idx, b, t] * resolution_hours <=
                    pe_vars.fuel_for_power_hourly[gen_idx][node, t] * mwhe_per_unit,
                    base_name = "pe_coupling_gen$(gen_idx)_$(b)_$(t)"
                )
                n_coupling_constraints += 1
            end
        end
    end

    # @debug not @info: per-window setup noise (see comment above).
    @debug "PE Coupling: Created $n_coupling_constraints constraints (resolution=$(resolution_hours)h)"
end

# =============================================================================
# Solution Extraction
# =============================================================================

"""
    extract_primary_energy_solution(model, vars, input, temporal)

Extract solution values from the primary energy model.
"""
function extract_primary_energy_solution(
    model::Model,
    vars::PrimaryEnergyVariables,
    input::PrimaryEnergyInput,
    temporal::TemporalMapping;
    adjusted_prices::Dict{String, Float64} = Dict{String, Float64}()
)::PrimaryEnergyResult
    fuel_names = [f.name for f in input.fuels]
    n_nodes = input.num_nodes
    n_primary = temporal.num_primary_periods
    n_invest = temporal.num_investment_periods

    # Investment results
    n_routes = length(input.transport_routes)
    transport_inv = Dict{String, Matrix{Float64}}()
    storage_inv = Dict{String, Matrix{Float64}}()

    for fuel_name in fuel_names
        if haskey(vars.transport_capacity_investment, fuel_name)
            transport_inv[fuel_name] = Matrix{Float64}(undef, n_invest, n_routes)
            for inv_p in 1:n_invest
                for r in 1:n_routes
                    transport_inv[fuel_name][inv_p, r] =
                        value(vars.transport_capacity_investment[fuel_name][inv_p, r])
                end
            end
        end

        if haskey(vars.storage_capacity_investment, fuel_name)
            storage_inv[fuel_name] = Matrix{Float64}(undef, n_invest, n_nodes)
            for inv_p in 1:n_invest
                for n in 1:n_nodes
                    storage_inv[fuel_name][inv_p, n] =
                        value(vars.storage_capacity_investment[fuel_name][inv_p, n])
                end
            end
        end
    end

    # Periodic aggregates
    total_supply = Dict{String, Vector{Float64}}()
    total_ne_satisfied = Dict{String, Vector{Float64}}()
    total_loss = Dict{String, Vector{Float64}}()

    for fuel_name in fuel_names
        total_supply[fuel_name] = zeros(n_primary)
        total_ne_satisfied[fuel_name] = zeros(n_primary)
        total_loss[fuel_name] = zeros(n_primary)

        for p in 1:n_primary
            for n in 1:n_nodes
                total_supply[fuel_name][p] +=
                    value(vars.fuel_supply_periodic[fuel_name][n, p])
                total_loss[fuel_name][p] +=
                    value(vars.fuel_loss_of_supply_periodic[fuel_name][n, p])
            end
            # Add hourly loss aggregated into primary periods
            if haskey(vars.fuel_loss_of_supply_hourly, fuel_name)
                hours_in_p = temporal.hours_in_primary_period[p]
                for t in hours_in_p, n in 1:n_nodes
                    total_loss[fuel_name][p] +=
                        value(vars.fuel_loss_of_supply_hourly[fuel_name][n, t])
                end
            end
        end

        # Aggregate non-electric consumption across sectors for this fuel
        for ((f, sector), ne_var) in vars.non_electric_consumption_periodic
            if f == fuel_name
                for p in 1:n_primary
                    for n in 1:n_nodes
                        total_ne_satisfied[fuel_name][p] +=
                            value(ne_var[n, p])
                    end
                end
            end
        end
    end

    # Final storage levels for carry-over
    final_storage = Dict{String, Vector{Float64}}()
    last_p = temporal.primary_period_indices[end]

    for fuel_name in fuel_names
        final_storage[fuel_name] = zeros(n_nodes)
        for n in 1:n_nodes
            final_storage[fuel_name][n] =
                value(vars.storage_level_end[fuel_name][n, last_p])
        end
    end

    # Per-route transport flows
    transport_flows = Dict{String, Matrix{Float64}}()
    for fuel_name in fuel_names
        if haskey(vars.fuel_transport_periodic, fuel_name) && n_routes > 0
            transport_flows[fuel_name] = Matrix{Float64}(undef, n_routes, n_primary)
            for r in 1:n_routes, p in 1:n_primary
                transport_flows[fuel_name][r, p] = value(vars.fuel_transport_periodic[fuel_name][r, p])
            end
        end
    end

    # Calculate actual costs from solved variables
    total_fuel_cost = 0.0
    total_transport_cost = 0.0
    total_loss_penalty = 0.0

    for fuel in input.fuels
        fuel_name = fuel.name
        price = get(adjusted_prices, fuel_name, fuel.price_base)

        # Fuel supply cost
        if haskey(vars.fuel_supply_periodic, fuel_name)
            for p in 1:n_primary, n in 1:n_nodes
                total_fuel_cost += value(vars.fuel_supply_periodic[fuel_name][n, p]) * price
            end
        end

        # Transport cost (route-based)
        if haskey(vars.fuel_transport_periodic, fuel_name)
            for p in 1:n_primary
                for (r, route) in enumerate(input.transport_routes)
                    if !haskey(route.fuel_params, fuel_name)
                        continue
                    end
                    flow_val = value(vars.fuel_transport_periodic[fuel_name][r, p])
                    if flow_val > 1e-6
                        fparams = route.fuel_params[fuel_name]
                        total_transport_cost += flow_val * fparams.transport_cost * route.distance_km
                    end
                end
            end
        end

        # Loss of supply penalty (periodic)
        if haskey(vars.fuel_loss_of_supply_periodic, fuel_name)
            for p in 1:n_primary, n in 1:n_nodes
                total_loss_penalty += value(vars.fuel_loss_of_supply_periodic[fuel_name][n, p]) *
                    input.loss_of_fuel_supply_penalty
            end
        end

        # Loss of supply penalty (hourly)
        if haskey(vars.fuel_loss_of_supply_hourly, fuel_name)
            for t in 1:input.hours, n in 1:n_nodes
                total_loss_penalty += value(vars.fuel_loss_of_supply_hourly[fuel_name][n, t]) *
                    input.loss_of_fuel_supply_penalty
            end
        end
    end

    return PrimaryEnergyResult(
        transport_inv,
        storage_inv,
        total_supply,
        total_ne_satisfied,
        total_loss,
        final_storage,
        transport_flows,
        total_fuel_cost,
        total_transport_cost,
        total_loss_penalty
    )
end

"""
    extract_hourly_results(vars, input, temporal)

Extract hourly operational results from the solved primary energy model.
Mirrors Python legacy `get_hourly_results()` (primary_energy.py line 1263).

Returns Dict with:
- "fuel_for_power_hourly" → Dict{Int, Matrix{Float64}} (gen_idx → [node, hour])
- "non_electric_consumption_hourly" → Dict{String, Dict{String, Matrix{Float64}}} (fuel → sector → [node, hour])
- "fuel_supply_periodic" → Dict{String, Matrix{Float64}} (fuel → [node, period])
- "fuel_storage_level_hourly" → Dict{String, Matrix{Float64}} (fuel → [node, hour+1])
"""
function extract_hourly_results(
    vars::PrimaryEnergyVariables,
    input::PrimaryEnergyInput,
    temporal::TemporalMapping
)::Dict{String, Any}
    n_nodes = input.num_nodes
    hours = input.hours
    n_primary = temporal.num_primary_periods
    results = Dict{String, Any}()

    # fuel_for_power_hourly: gen_idx → [node, hour] values
    fuel_power = Dict{Int, Matrix{Float64}}()
    for (gen_idx, mat) in vars.fuel_for_power_hourly
        fuel_power[gen_idx] = Matrix{Float64}(undef, n_nodes, hours)
        for n in 1:n_nodes, t in 1:hours
            fuel_power[gen_idx][n, t] = value(mat[n, t])
        end
    end
    results["fuel_for_power_hourly"] = fuel_power

    # non_electric_consumption_hourly: fuel → sector → [node, hour]
    ne_hourly = Dict{String, Dict{String, Matrix{Float64}}}()
    for ((fuel_id, sector), mat) in vars.non_electric_consumption_hourly
        if !haskey(ne_hourly, fuel_id)
            ne_hourly[fuel_id] = Dict{String, Matrix{Float64}}()
        end
        ne_hourly[fuel_id][sector] = Matrix{Float64}(undef, n_nodes, hours)
        for n in 1:n_nodes, t in 1:hours
            ne_hourly[fuel_id][sector][n, t] = value(mat[n, t])
        end
    end
    results["non_electric_consumption_hourly"] = ne_hourly

    # fuel_supply_periodic: fuel → [node, period]
    supply_periodic = Dict{String, Matrix{Float64}}()
    for (fuel_name, mat) in vars.fuel_supply_periodic
        supply_periodic[fuel_name] = Matrix{Float64}(undef, n_nodes, n_primary)
        for n in 1:n_nodes, p in 1:n_primary
            supply_periodic[fuel_name][n, p] = value(mat[n, p])
        end
    end
    results["fuel_supply_periodic"] = supply_periodic

    # fuel_storage_level_hourly: fuel → [node, hour+1]
    storage_hourly = Dict{String, Matrix{Float64}}()
    for (fuel_name, mat) in vars.fuel_storage_level_hourly
        n_steps = hours + 1  # hour 0 (initial) through hour H
        storage_hourly[fuel_name] = Matrix{Float64}(undef, n_nodes, n_steps)
        for n in 1:n_nodes, t in 1:n_steps
            storage_hourly[fuel_name][n, t] = value(mat[n, t])
        end
    end
    results["fuel_storage_level_hourly"] = storage_hourly

    return results
end

# =============================================================================
# Main API
# =============================================================================

"""
    create_primary_energy_model(model, input)

Create the primary energy optimization model components.

# Arguments
- `model::Model`: JuMP model to add variables and constraints to
- `input::PrimaryEnergyInput`: Primary energy configuration

# Returns
- `vars::PrimaryEnergyVariables`: Variable container
- `temporal::TemporalMapping`: Temporal period mapping
"""
function create_primary_energy_model(
    model::Model,
    input::PrimaryEnergyInput
)
    # Create temporal mapping
    temporal = create_temporal_mapping(
        input.hours,
        input.primary_energy_resolution,
        input.investment_resolution
    )

    # Prepare input data
    adjusted_prices, max_supply, ne_demand = prepare_fuel_data(input, temporal)

    # Create variables
    vars = build_primary_energy_variables!(model, input, temporal)

    # Add constraints
    add_primary_energy_constraints!(model, vars, input, temporal,
        adjusted_prices, max_supply, ne_demand)

    # Add electrolyzer constraints (E1: joint H2 production optimization)
    add_electrolyzer_constraints!(model, vars, input)

    return vars, temporal, adjusted_prices
end
