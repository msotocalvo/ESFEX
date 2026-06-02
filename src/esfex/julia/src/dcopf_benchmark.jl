"""
    solve_dcopf(; num_buses, demand, gen_bus, gen_cost, gen_max,
                  line_from, line_to, line_x, line_cap,
                  slack_bus, base_impedance,
                  gen_min, line_tap, line_shift)

Lightweight DC Optimal Power Flow solver for benchmarking.

Builds a minimal JuMP model with only the variables and constraints
required for standard DCOPF: generator output, voltage angles, and
line power flows.  Uses HiGHS as LP solver.

Supports transformer tap ratios and phase shifters following the
MATPOWER ``makeBdc`` convention:
  b_line = base_impedance / (x * tap)
  flow  = b_line * (θ_from - θ_to - shift_rad)

All bus indices are **1-based** (Julia convention).

# Returns
A `Dict{String, Any}` with keys:
- `"status"` — solver termination status string
- `"total_cost"` — optimal fuel cost (\$)
- `"angles_deg"` — voltage angle per bus (degrees)
- `"line_flows_mw"` — power flow per line (MW, positive = from→to)
- `"gen_dispatch_list"` — generation per generator (MW)
- `"gen_dispatch_mw"` — Dict(bus_0idx => MW) keyed by 0-indexed bus
"""
function solve_dcopf(;
    num_buses::Int,
    demand::AbstractVector{<:Real},
    gen_bus::AbstractVector{<:Integer},
    gen_cost::AbstractVector{<:Real},
    gen_max::AbstractVector{<:Real},
    gen_min::AbstractVector{<:Real} = zeros(Float64, length(gen_max)),
    line_from::AbstractVector{<:Integer},
    line_to::AbstractVector{<:Integer},
    line_x::AbstractVector{<:Real},
    line_cap::AbstractVector{<:Real},
    line_tap::AbstractVector{<:Real} = ones(Float64, length(line_x)),
    line_shift::AbstractVector{<:Real} = zeros(Float64, length(line_x)),
    slack_bus::Int,
    base_impedance::Real,
)::Dict{String, Any}

    # Convert to concrete Julia arrays for JuMP compatibility
    demand    = collect(Float64, demand)
    gen_bus   = collect(Int, gen_bus)
    gen_cost  = collect(Float64, gen_cost)
    gen_max   = collect(Float64, gen_max)
    gen_min   = collect(Float64, gen_min)
    line_from = collect(Int, line_from)
    line_to   = collect(Int, line_to)
    line_x    = collect(Float64, line_x)
    line_cap  = collect(Float64, line_cap)
    line_tap  = collect(Float64, line_tap)
    line_shift = collect(Float64, line_shift)
    base_impedance = Float64(base_impedance)

    n_bus  = num_buses
    n_gen  = length(gen_bus)
    n_line = length(line_from)

    # ── Model ────────────────────────────────────────────────────────────
    model = Model(create_optimizer(solver_name="highs", verbose=false))

    # ── Variables ────────────────────────────────────────────────────────
    @variable(model, pg[1:n_gen])             # generator output (MW)
    @variable(model, -π <= θ[1:n_bus] <= π)    # voltage angle (rad)
    @variable(model, pf[1:n_line])            # line flow (MW)

    # ── Generator capacity ───────────────────────────────────────────────
    for g in 1:n_gen
        @constraint(model, pg[g] >= gen_min[g])
        @constraint(model, pg[g] <= gen_max[g])
    end

    # ── Slack bus reference ──────────────────────────────────────────────
    @constraint(model, θ[slack_bus] == 0)

    # ── Flow-angle coupling: pf = b_line * (θ_from - θ_to - shift) ─────
    for l in 1:n_line
        b_line = base_impedance / (line_x[l] * line_tap[l])  # susceptance (MW/rad)
        shift_rad = deg2rad(line_shift[l])
        @constraint(model, pf[l] == b_line * (θ[line_from[l]] - θ[line_to[l]] - shift_rad))
    end

    # ── Line thermal limits (skip if cap ≤ 0 → unlimited, MATPOWER convention) ─
    for l in 1:n_line
        if line_cap[l] > 0.0
            @constraint(model, pf[l] <=  line_cap[l])
            @constraint(model, pf[l] >= -line_cap[l])
        end
    end

    # ── KCL: generation - demand == net outflow ──────────────────────────
    # Build incidence: K[bus, line] = +1 if from, -1 if to
    for b in 1:n_bus
        gen_at_bus = AffExpr(0.0)
        for g in 1:n_gen
            if gen_bus[g] == b
                add_to_expression!(gen_at_bus, 1.0, pg[g])
            end
        end

        flow_out = AffExpr(0.0)
        for l in 1:n_line
            if line_from[l] == b
                add_to_expression!(flow_out, 1.0, pf[l])
            elseif line_to[l] == b
                add_to_expression!(flow_out, -1.0, pf[l])
            end
        end

        @constraint(model, gen_at_bus - demand[b] == flow_out)
    end

    # ── Objective: minimise fuel cost ────────────────────────────────────
    @objective(model, Min, sum(gen_cost[g] * pg[g] for g in 1:n_gen))

    # ── Solve ────────────────────────────────────────────────────────────
    optimize!(model)

    status = string(termination_status(model))

    # ── Extract solution ─────────────────────────────────────────────────
    angles_deg   = [rad2deg(value(θ[b])) for b in 1:n_bus]
    flows_mw     = [value(pf[l]) for l in 1:n_line]
    gen_dispatch = [value(pg[g]) for g in 1:n_gen]
    total_cost   = objective_value(model)

    # Build gen_dispatch_mw keyed by 0-indexed bus (for Python compatibility)
    gen_mw = Dict{Int, Float64}()
    for g in 1:n_gen
        gen_mw[gen_bus[g] - 1] = gen_dispatch[g]
    end

    return Dict{String, Any}(
        "status"            => status,
        "total_cost"        => total_cost,
        "angles_deg"        => angles_deg,
        "line_flows_mw"     => flows_mw,
        "gen_dispatch_list" => gen_dispatch,
        "gen_dispatch_mw"   => gen_mw,
        "_solver_time"      => solve_time(model),
    )
end
