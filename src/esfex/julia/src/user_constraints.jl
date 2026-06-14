# user_constraints.jl
#
# Additive, user-defined constraints applied at the end of a model build.
# Two front doors share this one mechanism:
#   * Declarative config constraints  -> the built-in "linear" hook below.
#   * Plugin Julia overlays           -> call `register_constraint_hook!` at
#     include time to add their own constraint `type`s.
#
# A "spec" is a Dict coming from the Python side, e.g. for the linear hook:
#   Dict("name"=>"cap_coal", "type"=>"linear", "sense"=>"<=", "rhs"=>500.0,
#        "terms"=>[Dict("variable"=>"gen_output","index"=>[3,-1],"coefficient"=>1.0)])
# Index entries are 1-based; a `-1` means "sum over that axis" (e.g. all hours).

const CONSTRAINT_HOOKS = Dict{String,Function}()

"""
    register_constraint_hook!(type, fn)

Register a constraint hook. `fn` is called as `fn(model, vars, input, spec)` for
each user constraint whose `spec["type"] == type`.
"""
register_constraint_hook!(t::AbstractString, fn::Function) =
    (CONSTRAINT_HOOKS[String(t)] = fn)

"Remove all registered hooks (used by tests)."
clear_constraint_hooks!() = empty!(CONSTRAINT_HOOKS)

"""
    apply_user_constraints!(model, vars, input, specs)

Apply each spec by dispatching on `spec["type"]` to a registered hook. No-op
when `specs` is `nothing`/empty, so models without user constraints are
unchanged.
"""
function apply_user_constraints!(model::Model, vars, input, specs)
    (specs === nothing) && return
    for spec in specs
        t = String(get(spec, "type", "linear"))
        if !haskey(CONSTRAINT_HOOKS, t)
            nm = get(spec, "name", "?")
            error("Unknown custom constraint type '$t' (constraint '$nm'). " *
                  "Registered types: $(sort(collect(keys(CONSTRAINT_HOOKS))))")
        end
        CONSTRAINT_HOOKS[t](model, vars, input, spec)
    end
    return
end

# ── Built-in "linear" hook ────────────────────────────────────────────────

function _linear_constraint_hook!(model::Model, vars, input, spec)
    name = String(get(spec, "name", "user_constraint"))
    terms = get(spec, "terms", nothing)
    (terms === nothing || isempty(terms)) &&
        error("Linear custom constraint '$name' has no terms.")

    expr = AffExpr(0.0)
    for term in terms
        var = String(term["variable"])
        idx = Int[Int(i) for i in term["index"]]
        coeff = Float64(get(term, "coefficient", 1.0))
        add_to_expression!(expr, coeff, resolve_term(vars, input, var, idx, name))
    end

    rhs = Float64(spec["rhs"])
    sense = String(spec["sense"])
    if sense == "<="
        @constraint(model, expr <= rhs, base_name = name)
    elseif sense == ">="
        @constraint(model, expr >= rhs, base_name = name)
    elseif sense == "==" || sense == "="
        @constraint(model, expr == rhs, base_name = name)
    else
        error("Invalid sense '$sense' in custom constraint '$name' (use <=, >=, ==).")
    end
    return
end

# ── Variable resolvers (the documented "decision-variable registry") ──────
# Each returns an AffExpr (a sum of the referenced JuMP variables), expanding
# any `-1` index into a sum over that axis (and summing over a generator's /
# battery's active buses, which are an internal detail).

_hours(input) = 1:input.temporal.hours

function _hour_range(h::Int, input)
    h == -1 ? _hours(input) : (h:h)
end

"""
    resolve_term(vars::PowerSystemVariables, input, variable, idx, cname)

Operational decision variables. Supported `variable` names and their `idx`:
  * "gen_output"   [generator, hour]   (summed over the generator's buses)
  * "load_shed"    [node, hour]
  * "curtailment"  [node, hour]
  * "bat_charge" / "bat_discharge" / "bat_soc"  [battery, hour]
  * "power_flow"   [from_node, to_node, hour]
Use `-1` for a hour/generator index to sum over that axis.
"""
function resolve_term(vars::PowerSystemVariables, input, variable::String,
                      idx::Vector{Int}, cname::String)
    e = AffExpr(0.0)
    if variable == "gen_output"
        g, h = idx[1], idx[2]
        for bus in vars.buses_of_gen[g], hh in _hour_range(h, input)
            add_to_expression!(e, 1.0, vars.gen_output[g, bus, hh])
        end
    elseif variable in ("bat_charge", "bat_discharge", "bat_soc")
        arr = getfield(vars, Symbol(variable))
        bi, h = idx[1], idx[2]
        for bus in vars.buses_of_bat[bi], hh in _hour_range(h, input)
            add_to_expression!(e, 1.0, arr[bi, bus, hh])
        end
    elseif variable in ("load_shed", "curtailment")
        arr = getfield(vars, Symbol(variable))
        n, h = idx[1], idx[2]
        for hh in _hour_range(h, input)
            add_to_expression!(e, 1.0, arr[n, hh])
        end
    elseif variable == "power_flow"
        from, to, h = idx[1], idx[2], idx[3]
        flows = vars.power_flow[(from, to)]
        rng = h == -1 ? (1:length(flows)) : (h:h)
        for hh in rng
            add_to_expression!(e, 1.0, flows[hh])
        end
    else
        error("Unsupported operational variable '$variable' in custom " *
              "constraint '$cname'. Supported: gen_output, load_shed, " *
              "curtailment, bat_charge, bat_discharge, bat_soc, power_flow.")
    end
    return e
end

"""
    resolve_term(vars::MasterProblemVariables, input, variable, idx, cname)

Investment decision variables:
  * "tech_investment"            [year, tech, node]
  * "bat_tech_power_investment"  [year, tech, node]
  * "transfer_investment"        [year, from_node, to_node]
Use `-1` for year/tech/node to sum over that axis.
"""
function resolve_term(vars::MasterProblemVariables, input, variable::String,
                      idx::Vector{Int}, cname::String)
    e = AffExpr(0.0)
    if variable in ("tech_investment", "bat_tech_power_investment",
                    "bat_tech_capacity_investment")
        nested = getfield(vars, Symbol(variable))  # Dict{year}{tech} -> Vector{node}
        yr, tech, node = idx[1], idx[2], idx[3]
        years = yr == -1 ? collect(keys(nested)) : [yr]
        for y in years
            haskey(nested, y) || continue
            techs = tech == -1 ? collect(keys(nested[y])) : [tech]
            for t in techs
                haskey(nested[y], t) || continue
                vec = nested[y][t]
                nodes = node == -1 ? (1:length(vec)) : (node:node)
                for n in nodes
                    add_to_expression!(e, 1.0, vec[n])
                end
            end
        end
    elseif variable == "transfer_investment"
        nested = vars.transfer_investment  # Dict{year}{(from,to)} -> VariableRef
        yr, from, to = idx[1], idx[2], idx[3]
        years = yr == -1 ? collect(keys(nested)) : [yr]
        for y in years
            haskey(nested, y) || continue
            for (pair, ref) in nested[y]
                (from != -1 && pair[1] != from) && continue
                (to != -1 && pair[2] != to) && continue
                add_to_expression!(e, 1.0, ref)
            end
        end
    else
        error("Unsupported investment variable '$variable' in custom " *
              "constraint '$cname'. Supported: tech_investment, " *
              "bat_tech_power_investment, transfer_investment.")
    end
    return e
end

# Register the built-in declarative hook.
register_constraint_hook!("linear", _linear_constraint_hook!)
