"""
electrolyzer.jl - Electrolyzer Model for Green Hydrogen Production

Models power-to-hydrogen conversion with:
- Investment in new electrolyzer capacity
- Variable efficiency based on load
- Ramp constraints
- Hydrogen production output

This module integrates with PowerSystem for electricity consumption
and with PrimaryEnergy for hydrogen supply.
"""

using JuMP: @variable, @constraint, @expression, Model, AffExpr
using JuMP: value, VariableRef, add_to_expression!

# =============================================================================
# Variable Creation
# =============================================================================

"""
    build_electrolyzer_variables!(model, config, num_buses, num_hours; var_prefix="")

Create electrolyzer decision variables in the JuMP model.

# Arguments
- `model::Model`: JuMP model
- `config::ElectrolyzerConfig`: Electrolyzer configuration
- `num_buses::Int`: Number of buses
- `num_hours::Int`: Number of hours
- `var_prefix::String`: Optional prefix for variable names (for MasterProblem)

# Returns
- `ElectrolyzerVariables`: Container with all electrolyzer variables
"""
function build_electrolyzer_variables!(
    model::Model,
    config::ElectrolyzerConfig,
    num_buses::Int,
    num_hours::Int;
    var_prefix::String = ""
)
    # Investment variables
    investment = @variable(model,
        [b=1:num_buses],
        lower_bound = 0,
        upper_bound = config.invest_max_power[b],
        base_name = "$(var_prefix)electrolyzer_invest"
    )

    # Power consumption variables (bus × hour)
    power = @variable(model,
        [b=1:num_buses, t=1:num_hours],
        lower_bound = 0,
        base_name = "$(var_prefix)electrolyzer_power"
    )

    # Hydrogen production variables (bus × hour)
    h2_production = @variable(model,
        [b=1:num_buses, t=1:num_hours],
        lower_bound = 0,
        base_name = "$(var_prefix)h2_production"
    )

    return ElectrolyzerVariables(
        investment,
        power,
        h2_production
    )
end

# =============================================================================
# Constraints
# =============================================================================

"""
    add_electrolyzer_constraints!(model, vars, config, num_buses, num_hours)

Add electrolyzer operational constraints to the model.

# Arguments
- `model::Model`: JuMP model
- `vars::ElectrolyzerVariables`: Electrolyzer variables
- `config::ElectrolyzerConfig`: Electrolyzer configuration
- `num_buses::Int`: Number of buses
- `num_hours::Int`: Number of hours
"""
function add_electrolyzer_constraints!(
    model::Model,
    vars::ElectrolyzerVariables,
    config::ElectrolyzerConfig,
    num_buses::Int,
    num_hours::Int
)
    for b in 1:num_buses
        # Total capacity = existing + investment
        total_capacity = config.rated_power[b] + vars.investment[b]

        # Average efficiency
        avg_eff = (config.eff_at_rated[b] + config.eff_at_min[b]) / 2.0

        for t in 1:num_hours
            # Power limit: power <= total_capacity
            @constraint(model,
                vars.power[b, t] <= total_capacity,
                base_name = "electrolyzer_max_power_b$(b)_$(t)"
            )

            # H2 production: H2[kg] = Power[MW] * 1000 * eff / energy_per_kg_h2
            # energy_per_kg_h2 is in kWh/kg, so:
            # H2[kg/h] = Power[MW] * 1000[kW/MW] * eff / energy_per_kg_h2[kWh/kg]
            @constraint(model,
                vars.h2_production[b, t] ==
                vars.power[b, t] * 1000 * avg_eff / config.energy_per_kg_h2,
                base_name = "h2_production_b$(b)_$(t)"
            )

            # Ramp constraints (for t > 1)
            if t > 1
                # Ramp up: power[t] - power[t-1] <= total_capacity * ramp_up
                @constraint(model,
                    vars.power[b, t] - vars.power[b, t-1] <=
                    total_capacity * config.ramp_up[b],
                    base_name = "electrolyzer_ramp_up_b$(b)_$(t)"
                )

                # Ramp down: power[t-1] - power[t] <= total_capacity * ramp_down
                @constraint(model,
                    vars.power[b, t-1] - vars.power[b, t] <=
                    total_capacity * config.ramp_down[b],
                    base_name = "electrolyzer_ramp_down_b$(b)_$(t)"
                )
            end
        end
    end
end

# =============================================================================
# Objective Terms
# =============================================================================

"""
    get_electrolyzer_objective_terms(vars, config, num_buses, num_hours)

Calculate the objective function contribution from electrolyzer costs.

# Arguments
- `vars::ElectrolyzerVariables`: Electrolyzer variables
- `config::ElectrolyzerConfig`: Electrolyzer configuration
- `num_buses::Int`: Number of buses
- `num_hours::Int`: Number of hours

# Returns
- `AffExpr`: Expression representing electrolyzer costs
"""
function get_electrolyzer_objective_terms(
    vars::ElectrolyzerVariables,
    config::ElectrolyzerConfig,
    num_buses::Int,
    num_hours::Int
)::AffExpr
    cost_expr = AffExpr(0.0)

    for b in 1:num_buses
        # Investment cost (annualized, then scaled to simulation period)
        # Annualized investment = invest_cost / lifetime
        # Hourly share = annualized / HOURS_STD_YEAR
        # Total for simulation = hourly_share * num_hours
        if config.life_time[b] > 0
            hourly_inv_cost = config.invest_cost[b] / config.life_time[b] / Float64(HOURS_STD_YEAR)
            add_to_expression!(cost_expr, vars.investment[b], hourly_inv_cost * num_hours)
        end

        # Fixed O&M cost
        # Total capacity = existing + investment
        # Fixed cost = total_capacity * fixed_cost * num_hours
        add_to_expression!(cost_expr, config.rated_power[b] * config.fixed_cost[b] * num_hours)
        add_to_expression!(cost_expr, vars.investment[b], config.fixed_cost[b] * num_hours)

        # Variable costs (hourly)
        for t in 1:num_hours
            # Variable cost on power consumption
            add_to_expression!(cost_expr, vars.power[b, t], config.variable_cost[b])

            # Water cost on H2 production
            add_to_expression!(cost_expr, vars.h2_production[b, t], config.water_cost)
        end
    end

    return cost_expr
end

# =============================================================================
# Solution Extraction
# =============================================================================

"""
    extract_electrolyzer_solution(model, vars, config, num_buses, num_hours)

Extract solution values from a solved electrolyzer model.

# Arguments
- `model::Model`: Solved JuMP model
- `vars::ElectrolyzerVariables`: Electrolyzer variables
- `config::ElectrolyzerConfig`: Electrolyzer configuration
- `num_buses::Int`: Number of buses
- `num_hours::Int`: Number of hours

# Returns
- `ElectrolyzerResult`: Solution values
"""
function extract_electrolyzer_solution(
    model::Model,
    vars::ElectrolyzerVariables,
    config::ElectrolyzerConfig,
    num_buses::Int,
    num_hours::Int
)::ElectrolyzerResult
    # Extract investment values
    investment = [value(vars.investment[b]) for b in 1:num_buses]

    # Extract operational values
    power = Matrix{Float64}(undef, num_buses, num_hours)
    h2_production = Matrix{Float64}(undef, num_buses, num_hours)

    for b in 1:num_buses
        for t in 1:num_hours
            power[b, t] = value(vars.power[b, t])
            h2_production[b, t] = value(vars.h2_production[b, t])
        end
    end

    # Calculate totals
    total_investment = sum(investment)
    total_h2_produced = sum(h2_production)
    total_power_consumed = sum(power)

    return ElectrolyzerResult(
        investment,
        power,
        h2_production,
        total_investment,
        total_h2_produced,
        total_power_consumed
    )
end

