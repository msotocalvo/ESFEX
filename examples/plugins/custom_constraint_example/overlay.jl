# Plugin Julia overlay — included once after ESFEX.jl loads.
#
# Registers a "gen_cap" custom constraint type. A config custom_constraints entry
#   {name: cap_unit_1, type: gen_cap, params: {generator: 1, limit: 80.0}}
# caps that generator's total output (all its buses, all hours) at `limit` MWh.
#
# Hooks receive (model, vars, input, spec); `spec` is the constraint Dict with
# the plugin's `params` flattened in. The core ESFEX source is never modified.

using JuMP

ESFEX.register_constraint_hook!("gen_cap", function (model, vars, input, spec)
    g = Int(spec["generator"])
    limit = Float64(spec["limit"])
    name = String(get(spec, "name", "gen_cap"))
    expr = AffExpr(0.0)
    for bus in vars.buses_of_gen[g], h in 1:input.temporal.hours
        add_to_expression!(expr, 1.0, vars.gen_output[g, bus, h])
    end
    @constraint(model, expr <= limit, base_name = name)
end)
