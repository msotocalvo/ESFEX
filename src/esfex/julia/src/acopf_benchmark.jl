"""
    solve_acopf(; num_buses, demand_p, demand_q, shunt_g, shunt_b,
                  gen_bus, gen_cost, gen_pmax, gen_pmin, gen_qmax, gen_qmin,
                  line_from, line_to, line_r, line_x, line_b, line_cap,
                  line_tap, line_shift, vm_max, vm_min, slack_bus, base_mva)

Lightweight AC Optimal Power Flow solver for benchmarking.

Builds a minimal JuMP model with a compact polar NLP formulation and solves
it with Ipopt.  Flow expressions are substituted directly into the KCL
constraints (no auxiliary flow variables), matching the PowerModels
ACPPowerModel approach for optimal NLP sparsity.

Variables: Pg, Qg, Vm, Va.
Constraints: P/Q KCL (with inline trig flow expressions and shunt),
gen P/Q limits, voltage bounds, line apparent-power limits, slack angle = 0.

Works in per-unit internally; all inputs and outputs are in MW / MVAr / degrees.
All bus indices are **1-based** (Julia convention).

# Returns
A `Dict{String, Any}` with keys:
- `"status"`            — solver termination status string
- `"total_cost"`        — optimal fuel cost (\$)
- `"angles_deg"`        — voltage angle per bus (degrees)
- `"vm_pu"`             — voltage magnitude per bus (per-unit)
- `"line_flows_mw"`     — active power flow per line (MW, from→to)
- `"line_flows_mvar"`   — reactive power flow per line (MVAr, from→to)
- `"gen_dispatch_list"`  — active generation per generator (MW)
- `"gen_reactive_list"`  — reactive generation per generator (MVAr)
- `"gen_dispatch_mw"`    — Dict(bus_0idx => MW) keyed by 0-indexed bus
- `"_solver_time"`       — Ipopt solve time (s)
"""
function solve_acopf(;
    num_buses::Int,
    demand_p::AbstractVector{<:Real},
    demand_q::AbstractVector{<:Real},
    shunt_g::AbstractVector{<:Real},
    shunt_b::AbstractVector{<:Real},
    gen_bus::AbstractVector{<:Integer},
    gen_cost::AbstractVector{<:Real},
    gen_pmax::AbstractVector{<:Real},
    gen_pmin::AbstractVector{<:Real},
    gen_qmax::AbstractVector{<:Real},
    gen_qmin::AbstractVector{<:Real},
    line_from::AbstractVector{<:Integer},
    line_to::AbstractVector{<:Integer},
    line_r::AbstractVector{<:Real},
    line_x::AbstractVector{<:Real},
    line_b::AbstractVector{<:Real},
    line_cap::AbstractVector{<:Real},
    line_tap::AbstractVector{<:Real} = ones(Float64, length(line_from)),
    line_shift::AbstractVector{<:Real} = zeros(Float64, length(line_from)),
    vm_max::AbstractVector{<:Real} = fill(1.1, num_buses),
    vm_min::AbstractVector{<:Real} = fill(0.9, num_buses),
    vm_start::AbstractVector{<:Real} = Float64[],
    va_start::AbstractVector{<:Real} = Float64[],
    pg_start::AbstractVector{<:Real} = Float64[],
    slack_bus::Int,
    base_mva::Real,
)::Dict{String, Any}

    # ── Convert to concrete Julia arrays ─────────────────────────────
    demand_p   = collect(Float64, demand_p)
    demand_q   = collect(Float64, demand_q)
    shunt_g    = collect(Float64, shunt_g)
    shunt_b    = collect(Float64, shunt_b)
    gen_bus    = collect(Int, gen_bus)
    gen_cost   = collect(Float64, gen_cost)
    gen_pmax   = collect(Float64, gen_pmax)
    gen_pmin   = collect(Float64, gen_pmin)
    gen_qmax   = collect(Float64, gen_qmax)
    gen_qmin   = collect(Float64, gen_qmin)
    line_from  = collect(Int, line_from)
    line_to    = collect(Int, line_to)
    line_r     = collect(Float64, line_r)
    line_x     = collect(Float64, line_x)
    line_b_ch  = collect(Float64, line_b)
    line_cap   = collect(Float64, line_cap)
    line_tap   = collect(Float64, line_tap)
    line_shift = collect(Float64, line_shift)
    vm_max     = collect(Float64, vm_max)
    vm_min     = collect(Float64, vm_min)
    base_mva   = Float64(base_mva)

    n_bus  = num_buses
    n_gen  = length(gen_bus)
    n_line = length(line_from)

    # ── Per-unit conversion ──────────────────────────────────────────
    pd_pu    = demand_p ./ base_mva
    qd_pu    = demand_q ./ base_mva
    gs_pu    = shunt_g  ./ base_mva
    bs_pu    = shunt_b  ./ base_mva
    pgmax_pu = gen_pmax ./ base_mva
    pgmin_pu = gen_pmin ./ base_mva
    qgmax_pu = gen_qmax ./ base_mva
    qgmin_pu = gen_qmin ./ base_mva
    cap_pu   = line_cap ./ base_mva

    # ── Branch admittance (4-terminal π-model with tap & shift) ──────
    g_ff = Vector{Float64}(undef, n_line)
    b_ff = Vector{Float64}(undef, n_line)
    g_ft = Vector{Float64}(undef, n_line)
    b_ft = Vector{Float64}(undef, n_line)
    g_tt = Vector{Float64}(undef, n_line)
    b_tt = Vector{Float64}(undef, n_line)
    g_tf = Vector{Float64}(undef, n_line)
    b_tf = Vector{Float64}(undef, n_line)

    for l in 1:n_line
        r   = line_r[l]
        x   = line_x[l]
        bch = line_b_ch[l]
        tap = line_tap[l]
        φ   = deg2rad(line_shift[l])

        # Clamp tiny reactance to avoid division by zero
        if abs(x) < 1e-12
            x = copysign(1e-6, x == 0.0 ? 1.0 : x)
        end

        # Series admittance
        y_s = 1.0 / complex(r, x)
        gs  = real(y_s)
        bs  = imag(y_s)

        # General π-model with ideal transformer (MATPOWER convention)
        #   Y_ff = (y_s + j·bch/2) / tap²
        #   Y_ft = -y_s / (tap · e^{-jφ})
        #   Y_tt = y_s + j·bch/2
        #   Y_tf = -y_s / (tap · e^{jφ})
        cosφ = cos(φ)
        sinφ = sin(φ)

        g_ff[l] =  gs / tap^2
        b_ff[l] = (bs + bch / 2) / tap^2
        g_ft[l] = -(gs * cosφ - bs * sinφ) / tap
        b_ft[l] = -(gs * sinφ + bs * cosφ) / tap
        g_tt[l] =  gs
        b_tt[l] =  bs + bch / 2
        g_tf[l] = -(gs * cosφ + bs * sinφ) / tap
        b_tf[l] =  (gs * sinφ - bs * cosφ) / tap
    end

    # ── Pre-index: bus → generators, bus → lines ─────────────────────
    bus_gens = [Int[] for _ in 1:n_bus]
    for g in 1:n_gen
        push!(bus_gens[gen_bus[g]], g)
    end
    bus_lines_from = [Int[] for _ in 1:n_bus]
    bus_lines_to   = [Int[] for _ in 1:n_bus]
    for l in 1:n_line
        push!(bus_lines_from[line_from[l]], l)
        push!(bus_lines_to[line_to[l]], l)
    end

    # ── Model ────────────────────────────────────────────────────────
    model = Model(create_optimizer(
        solver_name = "ipopt",
        verbose     = false,
        time_limit  = 1800.0,
        gap         = 1e-6,
    ))
    set_optimizer_attribute(model, "max_iter", 10000)

    # ── Decision variables (per-unit) — compact: no flow variables ───
    @variable(model, pgmin_pu[g] <= pg[g = 1:n_gen] <= pgmax_pu[g])
    @variable(model, qgmin_pu[g] <= qg[g = 1:n_gen] <= qgmax_pu[g])
    @variable(model, vm_min[b]   <= vm[b = 1:n_bus] <= vm_max[b])
    @variable(model, -π <= va[b = 1:n_bus] <= π)

    # ── Warm start ─────────────────────────────────────────────────────
    _has_vm = length(vm_start) == n_bus
    _has_va = length(va_start) == n_bus
    _has_pg = length(pg_start) == n_gen

    for b in 1:n_bus
        vm0 = _has_vm ? clamp(Float64(vm_start[b]), vm_min[b], vm_max[b]) : 1.0
        va0 = _has_va ? deg2rad(Float64(va_start[b])) : 0.0
        set_start_value(vm[b], vm0)
        set_start_value(va[b], va0)
    end
    for g in 1:n_gen
        pg0 = _has_pg ? clamp(Float64(pg_start[g]) / base_mva, pgmin_pu[g], pgmax_pu[g]) :
                        (pgmin_pu[g] + pgmax_pu[g]) / 2
        set_start_value(pg[g], pg0)
        set_start_value(qg[g], (qgmin_pu[g] + qgmax_pu[g]) / 2)
    end

    # ── Slack bus reference angle ────────────────────────────────────
    @constraint(model, va[slack_bus] == 0)

    # ── Inline flow expression helpers ───────────────────────────────
    #   Pf_l = g_ff·Vm_i² + Vm_i·Vm_j·(g_ft·cos(θ_ij) + b_ft·sin(θ_ij))
    #   Qf_l = -b_ff·Vm_i² + Vm_i·Vm_j·(-b_ft·cos(θ_ij) + g_ft·sin(θ_ij))
    #   Pt_l = g_tt·Vm_j² + Vm_i·Vm_j·(g_tf·cos(θ_ij) - b_tf·sin(θ_ij))
    #   Qt_l = -b_tt·Vm_j² + Vm_i·Vm_j·(-b_tf·cos(θ_ij) - g_tf·sin(θ_ij))

    # ── KCL: Active power balance at each bus ────────────────────────
    #   Σ Pg − Pd − Gs·Vm² = Σ Pf(from lines) + Σ Pt(to lines)
    for b in 1:n_bus
        gen_p = AffExpr(-pd_pu[b])
        for g in bus_gens[b]
            add_to_expression!(gen_p, 1.0, pg[g])
        end
        @constraint(model,
            gen_p - gs_pu[b] * vm[b]^2
            == sum(
                g_ff[l] * vm[line_from[l]]^2 +
                vm[line_from[l]] * vm[line_to[l]] * (
                    g_ft[l] * cos(va[line_from[l]] - va[line_to[l]]) +
                    b_ft[l] * sin(va[line_from[l]] - va[line_to[l]])
                )
                for l in bus_lines_from[b]; init=0.0
            ) + sum(
                g_tt[l] * vm[line_to[l]]^2 +
                vm[line_from[l]] * vm[line_to[l]] * (
                    g_tf[l] * cos(va[line_from[l]] - va[line_to[l]]) -
                    b_tf[l] * sin(va[line_from[l]] - va[line_to[l]])
                )
                for l in bus_lines_to[b]; init=0.0
            )
        )
    end

    # ── KCL: Reactive power balance at each bus ──────────────────────
    #   Σ Qg − Qd + Bs·Vm² = Σ Qf(from lines) + Σ Qt(to lines)
    for b in 1:n_bus
        gen_q = AffExpr(-qd_pu[b])
        for g in bus_gens[b]
            add_to_expression!(gen_q, 1.0, qg[g])
        end
        @constraint(model,
            gen_q + bs_pu[b] * vm[b]^2
            == sum(
                -b_ff[l] * vm[line_from[l]]^2 +
                vm[line_from[l]] * vm[line_to[l]] * (
                    -b_ft[l] * cos(va[line_from[l]] - va[line_to[l]]) +
                     g_ft[l] * sin(va[line_from[l]] - va[line_to[l]])
                )
                for l in bus_lines_from[b]; init=0.0
            ) + sum(
                -b_tt[l] * vm[line_to[l]]^2 +
                vm[line_from[l]] * vm[line_to[l]] * (
                    -b_tf[l] * cos(va[line_from[l]] - va[line_to[l]]) -
                     g_tf[l] * sin(va[line_from[l]] - va[line_to[l]])
                )
                for l in bus_lines_to[b]; init=0.0
            )
        )
    end

    # ── Line thermal limits (apparent power, skip if cap ≤ 0) ────────
    for l in 1:n_line
        if cap_pu[l] > 0.0
            i, j = line_from[l], line_to[l]
            cap2 = cap_pu[l]^2

            # |S_f|² ≤ cap²
            @constraint(model,
                (g_ff[l] * vm[i]^2 + vm[i] * vm[j] * (g_ft[l] * cos(va[i] - va[j]) + b_ft[l] * sin(va[i] - va[j])))^2 +
                (-b_ff[l] * vm[i]^2 + vm[i] * vm[j] * (-b_ft[l] * cos(va[i] - va[j]) + g_ft[l] * sin(va[i] - va[j])))^2
                <= cap2
            )
            # |S_t|² ≤ cap²
            @constraint(model,
                (g_tt[l] * vm[j]^2 + vm[i] * vm[j] * (g_tf[l] * cos(va[i] - va[j]) - b_tf[l] * sin(va[i] - va[j])))^2 +
                (-b_tt[l] * vm[j]^2 + vm[i] * vm[j] * (-b_tf[l] * cos(va[i] - va[j]) - g_tf[l] * sin(va[i] - va[j])))^2
                <= cap2
            )
        end
    end

    # ── Objective: minimise fuel cost ($/h) ──────────────────────────
    @objective(model, Min, sum(gen_cost[g] * pg[g] * base_mva for g in 1:n_gen))

    # ── Solve ────────────────────────────────────────────────────────
    optimize!(model)

    status = string(termination_status(model))

    # ── Extract solution ─────────────────────────────────────────────
    angles_deg = [rad2deg(value(va[b])) for b in 1:n_bus]
    vm_pu_out  = [value(vm[b]) for b in 1:n_bus]

    # Compute line flows from solution values (no flow variables)
    flows_mw    = Vector{Float64}(undef, n_line)
    flows_mvar  = Vector{Float64}(undef, n_line)
    flows_to_mw = Vector{Float64}(undef, n_line)
    for l in 1:n_line
        i, j  = line_from[l], line_to[l]
        vmi   = vm_pu_out[i]
        vmj   = vm_pu_out[j]
        θij   = deg2rad(angles_deg[i]) - deg2rad(angles_deg[j])
        cosθ  = cos(θij)
        sinθ  = sin(θij)
        vv    = vmi * vmj

        flows_mw[l]    = (g_ff[l] * vmi^2 + g_ft[l] * vv * cosθ + b_ft[l] * vv * sinθ) * base_mva
        flows_mvar[l]  = (-b_ff[l] * vmi^2 - b_ft[l] * vv * cosθ + g_ft[l] * vv * sinθ) * base_mva
        flows_to_mw[l] = (g_tt[l] * vmj^2 + g_tf[l] * vv * cosθ - b_tf[l] * vv * sinθ) * base_mva
    end

    gen_dispatch = [value(pg[g]) * base_mva for g in 1:n_gen]
    gen_reactive = [value(qg[g]) * base_mva for g in 1:n_gen]
    total_cost   = objective_value(model)

    # gen_dispatch_mw keyed by 0-indexed bus (Python compatibility)
    gen_mw = Dict{Int, Float64}()
    for g in 1:n_gen
        gen_mw[gen_bus[g] - 1] = gen_dispatch[g]
    end

    return Dict{String, Any}(
        "status"             => status,
        "total_cost"         => total_cost,
        "angles_deg"         => angles_deg,
        "vm_pu"              => vm_pu_out,
        "line_flows_mw"      => flows_mw,
        "line_flows_mvar"    => flows_mvar,
        "line_flows_to_mw"   => flows_to_mw,
        "gen_dispatch_list"  => gen_dispatch,
        "gen_reactive_list"  => gen_reactive,
        "gen_dispatch_mw"    => gen_mw,
        "_solver_time"       => solve_time(model),
    )
end
