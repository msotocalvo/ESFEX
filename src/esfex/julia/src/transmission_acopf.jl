"""
transmission_acopf.jl - AC Optimal Power Flow Formulations

Implements multiple ACOPF formulations as JuMP constraints, integrated into
the ESFEX operational dispatch model. The user selects one via power_flow_mode:

  - "acopf_soc"   : Second-Order Cone relaxation (W-space)  — convex, HiGHS
  - "acopf_qc"    : Quadratic Convex relaxation (W-space + McCormick) — convex, HiGHS
  - "acopf_sdp"   : Semidefinite Programming relaxation — convex, SCS/Mosek
  - "acopf_polar"  : Polar NLP (exact, V-θ) — nonlinear, Ipopt
  - "acopf_rect"   : Rectangular NLP (exact, e-f) — nonlinear, Ipopt

All formulations share the same KCL structure (active + reactive power balance)
and differ only in how voltage-power flow relationships are modeled.

Reference:
  - Jabr (2006): Radial distribution load flow using conic programming
  - Coffrin et al. (2015): The QC relaxation
  - Bai et al. (2008): SDP relaxation of OPF
  - Cain et al. (2012): History of optimal power flow
"""

using JuMP: @variable, @constraint, @expression, Model, AffExpr, VariableRef
using JuMP: add_to_expression!, SecondOrderCone, RotatedSecondOrderCone, PSDCone
using JuMP: fix, value, has_values, set_objective_coefficient, set_start_value
import MathOptInterface as MOI

# =============================================================================
# Formulation Types (dispatch pattern)
# =============================================================================

abstract type ACOPFFormulation end
struct SOCFormulation     <: ACOPFFormulation end
struct QCFormulation      <: ACOPFFormulation end
struct SDPFormulation     <: ACOPFFormulation end
struct PolarNLPFormulation  <: ACOPFFormulation end
struct RectNLPFormulation   <: ACOPFFormulation end

function parse_acopf_formulation(mode::String)::ACOPFFormulation
    mode == "acopf_soc"   && return SOCFormulation()
    mode == "acopf_qc"    && return QCFormulation()
    mode == "acopf_sdp"   && return SDPFormulation()
    mode == "acopf_polar" && return PolarNLPFormulation()
    mode == "acopf_rect"  && return RectNLPFormulation()
    error("Unknown ACOPF formulation: $mode. " *
          "Valid: acopf_soc, acopf_qc, acopf_sdp, acopf_polar, acopf_rect")
end

# =============================================================================
# Network Data for ACOPF
# =============================================================================

"""
    ACOPFBranch

Precomputed admittance parameters for a single branch (line or transformer).

The 4-terminal admittance model:
    S_from = V_i × I_from*  where  I_from = Y_ff × V_i + Y_ft × V_j
    S_to   = V_j × I_to*    where  I_to   = Y_tf × V_i + Y_tt × V_j

In W-space:
    P_from = g_ff × w_ii + g_ft × wr + b_ft × wi
    Q_from = -b_ff × w_ii - b_ft × wr + g_ft × wi
    P_to   = g_tt × w_jj + g_tf × wr - b_tf × wi
    Q_to   = -b_tt × w_jj - b_tf × wr - g_tf × wi
"""
struct ACOPFBranch
    from_bus::Int
    to_bus::Int
    # From-side self admittance: Y_ff = (g_s + jb_s)/t² + j×b_sh/(2t²)
    g_ff::Float64
    b_ff::Float64
    # From→to mutual admittance: Y_ft = -(g_s + jb_s)/t
    g_ft::Float64
    b_ft::Float64
    # To-side self admittance: Y_tt = g_s + jb_s + j×b_sh/2
    g_tt::Float64
    b_tt::Float64
    # To→from mutual admittance: Y_tf = -(g_s + jb_s)/t
    g_tf::Float64
    b_tf::Float64
    # Thermal limit (MVA apparent power)
    capacity_mva::Float64
    # Tap ratio (1.0 for lines)
    tap::Float64
end

"""
    ACOPFNetwork

Precomputed network data for ACOPF formulations.
"""
struct ACOPFNetwork
    num_buses::Int
    branches::Vector{ACOPFBranch}
    # Index: for each bus, list of (branch_index, is_from_side) tuples
    bus_branches::Vector{Vector{Tuple{Int, Bool}}}
    slack_bus::Int
    base_mva::Float64
    v_min::Float64          # per-unit min voltage
    v_max::Float64          # per-unit max voltage
    max_angle_diff_rad::Float64
    # Y-bus matrix (for NLP bus-injection form and SDP)
    ybus::Matrix{ComplexF64}
end

"""
    ACOPFNetwork(network::NetworkConfig, input::PowerSystemInput)

Build ACOPF network data from the system configuration.
"""
function ACOPFNetwork(network::NetworkConfig, input::PowerSystemInput)
    n_bus = network.num_buses
    branches = ACOPFBranch[]

    # Minimum reactance to prevent extreme admittances from very short
    # lines/bus-ties. With x_min=0.01: max |b| = 100 p.u. per branch.
    # Ipopt handles these fine; conic solvers (SCS/Clarabel) struggle
    # with the resulting coefficient ranges but we now use NLP form.
    x_min_pu = input.acopf_min_reactance_pu

    # Process transmission lines
    for line in network.transmission_lines
        i, j = line.from_node, line.to_node
        i == j && continue  # skip self-loops

        nc = line.num_circuits
        r = line.resistance_pu / nc
        x = line.reactance_pu / nc
        b_sh = line.susceptance_pu * nc

        if abs(x) < 1e-12 && abs(r) < 1e-12
            continue
        end

        # Clamp small reactances to prevent numerical issues
        if abs(x) < x_min_pu
            x = sign(x) == 0 ? x_min_pu : copysign(x_min_pu, x)
        end

        y_s = 1.0 / complex(r, x)
        g_s = real(y_s)
        b_s = imag(y_s)

        # Line (tap = 1): Y_ff = y_s + j*b_sh/2, Y_ft = -y_s
        push!(branches, ACOPFBranch(
            i, j,
            g_s, b_s + b_sh/2,          # g_ff, b_ff
            -g_s, -b_s,                  # g_ft, b_ft
            g_s, b_s + b_sh/2,           # g_tt, b_tt
            -g_s, -b_s,                  # g_tf, b_tf
            line.capacity_mw * nc,       # capacity_mva (use MW ≈ MVA for consistency)
            1.0                          # tap
        ))
    end

    # Process transformers
    for trafo in network.transformers
        i, j = trafo.from_node, trafo.to_node
        r = trafo.resistance_pu
        x = trafo.reactance_pu
        t = trafo.tap_ratio

        if abs(x) < 1e-12
            x = trafo.impedance_pu * 0.99
        end
        if abs(t) < 1e-12
            t = 1.0
        end

        # Normalize tap ratios that represent voltage level changes (e.g.
        # 480V/34.5kV = 0.014) to 1.0.  In a single-base per-unit system,
        # extreme taps create enormous admittances that make the ACOPF
        # numerically intractable.  Setting tap=1.0 treats the transformer
        # as a simple series impedance — an appropriate approximation for
        # operational dispatch when not modeling voltage regulation.
        if t < input.acopf_tap_ratio_min || t > input.acopf_tap_ratio_max
            t = 1.0
        end

        y_s = 1.0 / complex(r, x)
        g_s = real(y_s)
        b_s = imag(y_s)

        push!(branches, ACOPFBranch(
            i, j,
            g_s / t^2, b_s / t^2,       # g_ff, b_ff
            -g_s / t, -b_s / t,          # g_ft, b_ft
            g_s, b_s,                     # g_tt, b_tt
            -g_s / t, -b_s / t,          # g_tf, b_tf
            trafo.rated_power_mva,       # capacity_mva
            t                            # tap
        ))
    end

    # Build bus → branch index
    bus_branches = [Tuple{Int,Bool}[] for _ in 1:n_bus]
    for (l, br) in enumerate(branches)
        push!(bus_branches[br.from_bus], (l, true))
        push!(bus_branches[br.to_bus], (l, false))
    end

    # Build Y-bus from existing function (for NLP and SDP)
    ybus = build_ybus(network.transmission_lines, network.transformers, n_bus)

    # Read ACOPF parameters from input
    base_mva = input.acopf_base_mva
    v_min = input.acopf_v_min
    v_max = input.acopf_v_max

    return ACOPFNetwork(
        n_bus, branches, bus_branches,
        network.slack_bus, base_mva, v_min, v_max,
        network.max_angle_diff_rad, ybus
    )
end

# =============================================================================
# ACOPF Variables Container
# =============================================================================

"""
    ACOPFVariables

Container for ACOPF-specific decision variables. Only the fields relevant
to the selected formulation are populated; others are `nothing`.
"""
mutable struct ACOPFVariables
    formulation::String

    # W-space variables (SOC / QC / SDP)
    w::Union{Matrix{VariableRef}, Nothing}               # V² diagonal [bus, hour]
    wr::Union{Matrix{VariableRef}, Nothing}               # Re(V_i⋅V_j*) [branch, hour]
    wi::Union{Matrix{VariableRef}, Nothing}               # Im(V_i⋅V_j*) [branch, hour]

    # Full W matrix for SDP (all bus pairs, upper triangle)
    # Dict[(i,j)] → [hour] where i < j for off-diagonal
    wr_full::Union{Dict{Tuple{Int,Int}, Vector{VariableRef}}, Nothing}
    wi_full::Union{Dict{Tuple{Int,Int}, Vector{VariableRef}}, Nothing}

    # Polar NLP variables
    vm::Union{Matrix{VariableRef}, Nothing}               # |V| [bus, hour]
    va::Union{Matrix{VariableRef}, Nothing}               # θ   [bus, hour]

    # Rectangular NLP variables
    vr::Union{Matrix{VariableRef}, Nothing}               # e = V cos θ [bus, hour]
    vi_rect::Union{Matrix{VariableRef}, Nothing}          # f = V sin θ [bus, hour]

    # Reactive generation (all formulations) — SparseAxisArray [gen, bus, hour]
    q_gen::Any

    # Reactive power balance constraint refs (for dual extraction)
    q_balance_constraints::Union{Dict{Tuple{Int,Int}, Any}, Nothing}
end

function ACOPFVariables(formulation::String)
    return ACOPFVariables(
        formulation,
        nothing, nothing, nothing,    # w, wr, wi
        nothing, nothing,             # wr_full, wi_full
        nothing, nothing,             # vm, va
        nothing, nothing,             # vr, vi_rect
        nothing,                      # q_gen
        Dict{Tuple{Int,Int}, Any}()   # q_balance_constraints
    )
end

# =============================================================================
# Variable Construction (per formulation)
# =============================================================================

"""
    build_acopf_variables!(model, net, vars, input, formulation) → ACOPFVariables

Create ACOPF decision variables appropriate for the selected formulation.
"""
function build_acopf_variables!(model::Model, net::ACOPFNetwork,
                                 vars::PowerSystemVariables,
                                 input::PowerSystemInput,
                                 formulation::ACOPFFormulation)
    n_bus = net.num_buses
    n_br = length(net.branches)
    hours = size(input.demand, 1)
    av = ACOPFVariables(input.power_flow_mode)

    # --- Reactive generation variables (shared across all formulations) ---
    # Sparse: only for gen-bus pairs with capacity
    q_gen_pairs = Tuple{Int,Int,Int}[]
    for g in 1:length(input.generators)
        for i in vars.buses_of_gen[g]
            for t in 1:hours
                push!(q_gen_pairs, (g, i, t))
            end
        end
    end

    if !isempty(q_gen_pairs)
        av.q_gen = @variable(model, [idx in q_gen_pairs],
            base_name = "q_gen",
            lower_bound = _q_min_gen(input, idx[1], idx[2]),
            upper_bound = _q_max_gen(input, idx[1], idx[2])
        )
    else
        av.q_gen = nothing
    end

    # --- Formulation-specific voltage variables ---
    _build_voltage_variables!(model, net, av, input, hours, formulation)

    return av
end

# Q limit helpers
# Note: rated_power is per-bus (not per-node) when num_buses > num_nodes.
# Always index by bus, not by bus_to_node[bus].
function _q_max_gen(input::PowerSystemInput, g::Int, bus::Int)
    if haskey(input.gen_q_limits, g)
        node = input.network.bus_to_node[bus]
        if node <= length(input.gen_q_limits[g])
            return input.gen_q_limits[g][node]
        end
    end
    # Estimate from power factor: Q_max = P × tan(acos(pf))
    rated = bus <= length(input.generators[g].rated_power) ?
        input.generators[g].rated_power[bus] : 0.0
    pf = input.acopf_default_power_factor
    return rated * tan(acos(clamp(pf, 0.1, 1.0)))
end

function _q_min_gen(input::PowerSystemInput, g::Int, bus::Int)
    if haskey(input.gen_q_limits_min, g)
        node = input.network.bus_to_node[bus]
        if node <= length(input.gen_q_limits_min[g])
            return input.gen_q_limits_min[g][node]
        end
    end
    # Default: can absorb up to ratio × Q_max
    return -input.acopf_q_min_ratio * _q_max_gen(input, g, bus)
end

# ---------------------------------------------------------------------------
# SOC / QC variables: W-space
# ---------------------------------------------------------------------------
function _build_voltage_variables!(model, net, av, input, hours,
                                    ::Union{SOCFormulation, QCFormulation})
    n_bus = net.num_buses
    n_br = length(net.branches)

    # w[i,t] = V_i² (voltage magnitude squared)
    av.w = @variable(model, [i=1:n_bus, t=1:hours],
        lower_bound = net.v_min^2,
        upper_bound = net.v_max^2,
        base_name = "w")

    # Fix slack bus voltage to 1.0 p.u.
    for t in 1:hours
        fix(av.w[net.slack_bus, t], 1.0; force=true)
    end

    # wr[l,t] = V_i × V_j × cos(θ_i - θ_j)  for branch l
    wr_lb = -net.v_max^2  # Minimum possible (cos = -1)
    wr_ub = net.v_max^2   # Maximum possible (cos = 1)
    av.wr = @variable(model, [l=1:n_br, t=1:hours],
        lower_bound = wr_lb,
        upper_bound = wr_ub,
        base_name = "wr")

    # wi[l,t] = V_i × V_j × sin(θ_i - θ_j)  for branch l
    av.wi = @variable(model, [l=1:n_br, t=1:hours],
        lower_bound = -net.v_max^2,
        upper_bound = net.v_max^2,
        base_name = "wi")

    # Flat voltage start: V=1 p.u., θ=0 → w=1, wr=1, wi=0
    for i in 1:n_bus, t in 1:hours
        set_start_value(av.w[i, t], 1.0)
    end
    for l in 1:n_br, t in 1:hours
        set_start_value(av.wr[l, t], 1.0)
        set_start_value(av.wi[l, t], 0.0)
    end
end

# ---------------------------------------------------------------------------
# SDP variables: W-space + full matrix
# ---------------------------------------------------------------------------
function _build_voltage_variables!(model, net, av, input, hours, ::SDPFormulation)
    n_bus = net.num_buses

    # Warn for large systems
    if n_bus > 30
        @warn "SDP relaxation with $n_bus buses creates a $(2*n_bus)×$(2*n_bus) PSD constraint. " *
              "Consider using acopf_soc or acopf_qc for better performance."
    end

    # Diagonal: w[i,t] = V_i²
    av.w = @variable(model, [i=1:n_bus, t=1:hours],
        lower_bound = net.v_min^2,
        upper_bound = net.v_max^2,
        base_name = "w")

    for t in 1:hours
        fix(av.w[net.slack_bus, t], 1.0; force=true)
    end

    # Branch-indexed wr/wi (for power flow expressions in KCL)
    n_br = length(net.branches)
    av.wr = @variable(model, [l=1:n_br, t=1:hours],
        lower_bound = -net.v_max^2, upper_bound = net.v_max^2,
        base_name = "wr")
    av.wi = @variable(model, [l=1:n_br, t=1:hours],
        lower_bound = -net.v_max^2, upper_bound = net.v_max^2,
        base_name = "wi")

    # Full off-diagonal W for PSD constraint: all (i,j) pairs with i < j
    av.wr_full = Dict{Tuple{Int,Int}, Vector{VariableRef}}()
    av.wi_full = Dict{Tuple{Int,Int}, Vector{VariableRef}}()

    # Map from (from_bus, to_bus) → branch index (for linking)
    branch_map = Dict{Tuple{Int,Int}, Int}()
    for (l, br) in enumerate(net.branches)
        branch_map[(br.from_bus, br.to_bus)] = l
    end

    for i in 1:n_bus
        for j in (i+1):n_bus
            key = (i, j)
            if haskey(branch_map, (i, j))
                # This pair has a branch — link to branch-indexed variables
                l = branch_map[(i, j)]
                av.wr_full[key] = [av.wr[l, t] for t in 1:hours]
                av.wi_full[key] = [av.wi[l, t] for t in 1:hours]
            elseif haskey(branch_map, (j, i))
                # Branch exists in reverse direction
                l = branch_map[(j, i)]
                # W_ij = conj(W_ji), so wr is same, wi is negated
                av.wr_full[key] = [av.wr[l, t] for t in 1:hours]
                av.wi_full[key] = [@variable(model,
                    lower_bound=-net.v_max^2, upper_bound=net.v_max^2,
                    base_name="wi_full_$(i)_$(j)_$(t)") for t in 1:hours]
                # Link: wi_full[(i,j)] = -wi[(j,i)]
                for t in 1:hours
                    @constraint(model, av.wi_full[key][t] == -av.wi[l, t])
                end
            else
                # No branch — free variables constrained only by PSD
                av.wr_full[key] = [@variable(model,
                    lower_bound=-net.v_max^2, upper_bound=net.v_max^2,
                    base_name="wr_full_$(i)_$(j)_$(t)") for t in 1:hours]
                av.wi_full[key] = [@variable(model,
                    lower_bound=-net.v_max^2, upper_bound=net.v_max^2,
                    base_name="wi_full_$(i)_$(j)_$(t)") for t in 1:hours]
            end
        end
    end
end

# ---------------------------------------------------------------------------
# Polar NLP variables: V, θ
# ---------------------------------------------------------------------------
function _build_voltage_variables!(model, net, av, input, hours, ::PolarNLPFormulation)
    n_bus = net.num_buses

    av.vm = @variable(model, [i=1:n_bus, t=1:hours],
        lower_bound = net.v_min,
        upper_bound = net.v_max,
        base_name = "vm")

    av.va = @variable(model, [i=1:n_bus, t=1:hours],
        lower_bound = -π, upper_bound = π,
        base_name = "va")

    # Fix slack bus
    for t in 1:hours
        fix(av.vm[net.slack_bus, t], 1.0; force=true)
        fix(av.va[net.slack_bus, t], 0.0; force=true)
    end

    # Flat voltage start: V=1, θ=0
    for i in 1:n_bus, t in 1:hours
        set_start_value(av.vm[i, t], 1.0)
        set_start_value(av.va[i, t], 0.0)
    end
end

# ---------------------------------------------------------------------------
# Rectangular NLP variables: e, f
# ---------------------------------------------------------------------------
function _build_voltage_variables!(model, net, av, input, hours, ::RectNLPFormulation)
    n_bus = net.num_buses

    # e = V cos θ,  f = V sin θ
    av.vr = @variable(model, [i=1:n_bus, t=1:hours],
        lower_bound = -net.v_max,
        upper_bound = net.v_max,
        base_name = "vr")

    av.vi_rect = @variable(model, [i=1:n_bus, t=1:hours],
        lower_bound = -net.v_max,
        upper_bound = net.v_max,
        base_name = "vi_rect")

    # Fix slack bus: V = 1.0, θ = 0 → e = 1.0, f = 0.0
    for t in 1:hours
        fix(av.vr[net.slack_bus, t], 1.0; force=true)
        fix(av.vi_rect[net.slack_bus, t], 0.0; force=true)
    end

    # Set flat voltage starting point: e = 1.0, f = 0.0 (V=1, θ=0)
    # Critical for convergence — without this, Ipopt often finds
    # LOCALLY_INFEASIBLE because the default start (0,0) violates
    # the voltage magnitude lower bound.
    for i in 1:n_bus
        for t in 1:hours
            set_start_value(av.vr[i, t], 1.0)
            set_start_value(av.vi_rect[i, t], 0.0)
        end
    end

    # Voltage magnitude bounds: v_min² ≤ e² + f² ≤ v_max²
    for i in 1:n_bus
        i == net.slack_bus && continue
        for t in 1:hours
            @constraint(model,
                av.vr[i,t]^2 + av.vi_rect[i,t]^2 >= net.v_min^2,
                base_name = "vm_lb_$(i)_$(t)")
            @constraint(model,
                av.vr[i,t]^2 + av.vi_rect[i,t]^2 <= net.v_max^2,
                base_name = "vm_ub_$(i)_$(t)")
        end
    end
end

# =============================================================================
# Voltage Linking Constraints (formulation-specific)
# =============================================================================

"""
    add_acopf_voltage_constraints!(model, net, av, hours, formulation)

Add formulation-specific constraints that link voltage variables to power flow.
"""
function add_acopf_voltage_constraints!(model, net, av, hours, ::SOCFormulation)
    # SOC relaxation: wr² + wi² ≤ w_i × w_j (per branch).
    # Use JuMP's canonical RotatedSecondOrderCone set — Clarabel, Mosek,
    # Gurobi and HiGHS-conic accept it natively, and MOI's bridge layer
    # expands it to the equivalent quadratic inequality for Ipopt/NLP.
    # The cone {(t, u, x) : 2·t·u ≥ ‖x‖², t ≥ 0, u ≥ 0} with t=w_i/2,
    # u=w_j, x=[wr, wi] gives exactly 2·(w_i/2)·w_j = w_i·w_j ≥ wr² + wi².
    for (l, br) in enumerate(net.branches)
        i, j = br.from_bus, br.to_bus
        for t in 1:hours
            @constraint(model,
                [0.5 * av.w[i,t], av.w[j,t], av.wr[l,t], av.wi[l,t]]
                    in RotatedSecondOrderCone(),
                base_name = "soc_$(l)_$(t)")
        end
    end
end

function add_acopf_voltage_constraints!(model, net, av, hours, ::QCFormulation)
    # QC = SOC + McCormick envelopes + trigonometric bounds
    n_br = length(net.branches)

    # First add SOC constraints
    add_acopf_voltage_constraints!(model, net, av, hours, SOCFormulation())

    # McCormick envelopes for wr = V_i × V_j × cos(θ_ij)
    # Using bounds: v_min ≤ V_i ≤ v_max, -θ_max ≤ θ_ij ≤ θ_max
    θ_max = net.max_angle_diff_rad
    cos_lb = cos(θ_max)  # cos is even, min at ±θ_max
    cos_ub = 1.0

    for (l, br) in enumerate(net.branches)
        i, j = br.from_bus, br.to_bus
        v_lo = net.v_min
        v_hi = net.v_max

        for t in 1:hours
            # Tighter bounds on wr using cos bounds:
            # wr = V_i × V_j × cos(θ_ij)
            # wr_lb = v_lo² × cos_lb  (both V at min, cos at min)
            # wr_ub = v_hi² × cos_ub  (both V at max, cos at max)
            wr_lb_tight = v_lo * v_lo * cos_lb
            wr_ub_tight = v_hi * v_hi * cos_ub

            @constraint(model, av.wr[l,t] >= wr_lb_tight,
                base_name = "qc_wr_lb_$(l)_$(t)")
            @constraint(model, av.wr[l,t] <= wr_ub_tight,
                base_name = "qc_wr_ub_$(l)_$(t)")

            # McCormick envelopes for wr ≈ √(w_i) × √(w_j) × cos(θ_ij)
            # Convex envelope: wr ≥ v_lo² × cos_lb when both V at lower bound
            # These are implied by the tighter bounds above for SOC

            # Tighter bounds on wi using sin bounds:
            # wi = V_i × V_j × sin(θ_ij)
            # |sin(θ_ij)| ≤ sin(θ_max) for |θ_ij| ≤ θ_max
            sin_max = sin(θ_max)
            wi_bound = v_hi * v_hi * sin_max

            @constraint(model, av.wi[l,t] >= -wi_bound,
                base_name = "qc_wi_lb_$(l)_$(t)")
            @constraint(model, av.wi[l,t] <= wi_bound,
                base_name = "qc_wi_ub_$(l)_$(t)")

            # Convex envelope for cos:
            # wr ≥ (w_i + w_j)/2 × cos_lb  (when V_i = V_j)
            @constraint(model,
                av.wr[l,t] >= 0.5 * (av.w[i,t] + av.w[j,t]) * cos_lb,
                base_name = "qc_cos_env_$(l)_$(t)")

            # Angle difference limits via wi and wr relationship:
            # |tan(θ_ij)| ≤ tan(θ_max)  →  |wi| ≤ tan(θ_max) × wr
            if θ_max < π/2
                tan_max = tan(θ_max)
                @constraint(model,
                    av.wi[l,t] <= tan_max * av.wr[l,t],
                    base_name = "qc_angle_ub_$(l)_$(t)")
                @constraint(model,
                    av.wi[l,t] >= -tan_max * av.wr[l,t],
                    base_name = "qc_angle_lb_$(l)_$(t)")
            end
        end
    end
end

function add_acopf_voltage_constraints!(model, net, av, hours, ::SDPFormulation)
    n_bus = net.num_buses

    # Add per-branch SOC constraints (baseline)
    add_acopf_voltage_constraints!(model, net, av, hours, SOCFormulation())

    # Full W matrix PSD constraint (tighter than per-branch SOC)
    # W_real[i,j] = wr(i,j), W_imag[i,j] = wi(i,j)
    # The real representation [W_R  -W_I; W_I  W_R] ∈ PSD

    for t in 1:hours
        # Build the 2n × 2n real matrix using uniform AffExpr type
        # to avoid Vector{AbstractJuMPScalar} from mixed VariableRef + AffExpr
        M = Matrix{AffExpr}(undef, 2*n_bus, 2*n_bus)

        for i in 1:n_bus
            for j in 1:n_bus
                if i == j
                    # Diagonal: w[i,t] — convert VariableRef to AffExpr
                    M[i, j] = AffExpr(0.0, av.w[i, t] => 1.0)
                    M[i + n_bus, j + n_bus] = AffExpr(0.0, av.w[i, t] => 1.0)
                    M[i, j + n_bus] = AffExpr(0.0)
                    M[i + n_bus, j] = AffExpr(0.0)
                else
                    # Off-diagonal: wr, wi
                    ii, jj = min(i,j), max(i,j)
                    key = (ii, jj)
                    if haskey(av.wr_full, key)
                        # Convert VariableRef → AffExpr for uniform matrix type
                        wr_val = AffExpr(0.0, av.wr_full[key][t] => 1.0)
                        wi_val = AffExpr(0.0, av.wi_full[key][t] => 1.0)
                        # W_R is symmetric: W_R[i,j] = W_R[j,i] = wr
                        M[i, j] = copy(wr_val)
                        M[j, i] = copy(wr_val)
                        # W_I is antisymmetric: W_I[i,j] = wi, W_I[j,i] = -wi
                        sign = i < j ? 1.0 : -1.0
                        # [W_R  -W_I; W_I  W_R]
                        M[i, j + n_bus] = -sign * wi_val
                        M[j, i + n_bus] = sign * wi_val
                        M[i + n_bus, j] = sign * wi_val
                        M[j + n_bus, i] = -sign * wi_val
                        M[i + n_bus, j + n_bus] = copy(wr_val)
                        M[j + n_bus, i + n_bus] = copy(wr_val)
                    else
                        M[i, j] = AffExpr(0.0)
                        M[i, j + n_bus] = AffExpr(0.0)
                        M[i + n_bus, j] = AffExpr(0.0)
                        M[i + n_bus, j + n_bus] = AffExpr(0.0)
                    end
                end
            end
        end

        # Build symmetric JuMP matrix expression
        W_mat = @expression(model, [i=1:2*n_bus, j=1:2*n_bus], M[i, j])
        @constraint(model, W_mat in PSDCone(),
            base_name = "psd_$(t)")
    end
end

function add_acopf_voltage_constraints!(model, net, av, hours, ::PolarNLPFormulation)
    # Polar NLP: angle difference limits (the only linking constraint;
    # power flow is directly expressed in terms of V, θ in the KCL)
    if net.max_angle_diff_rad < π
        for (l, br) in enumerate(net.branches)
            for t in 1:hours
                @constraint(model,
                    av.va[br.from_bus, t] - av.va[br.to_bus, t] <=
                    net.max_angle_diff_rad,
                    base_name = "angle_ub_$(l)_$(t)")
                @constraint(model,
                    av.va[br.from_bus, t] - av.va[br.to_bus, t] >=
                    -net.max_angle_diff_rad,
                    base_name = "angle_lb_$(l)_$(t)")
            end
        end
    end
end

function add_acopf_voltage_constraints!(model, net, av, hours, ::RectNLPFormulation)
    # Rectangular NLP: angle limits expressed as linear constraints on e, f
    # tan(-θ_max) ≤ f_i/e_i - f_j/e_j ... too complex. Use direct form:
    # |θ_ij| ≤ θ_max  →  |atan2(fi*ej - ei*fj, ei*ej + fi*fj)| ≤ θ_max
    # Linearized: (fi*ej - ei*fj) ≤ tan(θ_max) × (ei*ej + fi*fj)
    if net.max_angle_diff_rad < π
        tan_max = tan(net.max_angle_diff_rad)
        for (l, br) in enumerate(net.branches)
            i, j = br.from_bus, br.to_bus
            for t in 1:hours
                # wi_ij = fi*ej - ei*fj (cross product ~ sin(θ_ij) × Vi × Vj)
                wi_expr = av.vi_rect[i,t] * av.vr[j,t] - av.vr[i,t] * av.vi_rect[j,t]
                # wr_ij = ei*ej + fi*fj (dot product ~ cos(θ_ij) × Vi × Vj)
                wr_expr = av.vr[i,t] * av.vr[j,t] + av.vi_rect[i,t] * av.vi_rect[j,t]

                @constraint(model,
                    wi_expr <= tan_max * wr_expr,
                    base_name = "angle_ub_rect_$(l)_$(t)")
                @constraint(model,
                    wi_expr >= -tan_max * wr_expr,
                    base_name = "angle_lb_rect_$(l)_$(t)")
            end
        end
    end
end

# =============================================================================
# Power Flow Expressions (per formulation)
# =============================================================================

# Helper: build P_from, Q_from, P_to, Q_to expressions for a branch at hour t

# --- W-space (SOC / QC / SDP) — affine expressions in w, wr, wi ---
function _branch_flow_expressions(net::ACOPFNetwork, av::ACOPFVariables,
                                   l::Int, t::Int,
                                   ::Union{SOCFormulation, QCFormulation, SDPFormulation})
    br = net.branches[l]
    i, j = br.from_bus, br.to_bus

    # These are AffExpr via JuMP operator overloading (Float64 * VariableRef)
    p_from = br.g_ff * av.w[i,t] + br.g_ft * av.wr[l,t] + br.b_ft * av.wi[l,t]
    q_from = -br.b_ff * av.w[i,t] - br.b_ft * av.wr[l,t] + br.g_ft * av.wi[l,t]
    p_to   = br.g_tt * av.w[j,t] + br.g_tf * av.wr[l,t] - br.b_tf * av.wi[l,t]
    q_to   = -br.b_tt * av.w[j,t] - br.b_tf * av.wr[l,t] - br.g_tf * av.wi[l,t]

    return p_from, q_from, p_to, q_to
end

# --- Polar NLP ---
function _branch_flow_expressions(net::ACOPFNetwork, av::ACOPFVariables,
                                   l::Int, t::Int, ::PolarNLPFormulation)
    br = net.branches[l]
    i, j = br.from_bus, br.to_bus

    # Use precomputed admittance coefficients with polar variables
    # P_from = g_ff × V_i² + V_i × V_j × (g_ft × cos(θ_ij) + b_ft × sin(θ_ij))
    # Note: g_ft is negative (-g_s/t), b_ft is negative (-b_s/t)
    Vi, Vj = av.vm[i, t], av.vm[j, t]
    θ_ij = av.va[i, t] - av.va[j, t]

    p_from = br.g_ff * Vi^2 + Vi * Vj * (br.g_ft * cos(θ_ij) + br.b_ft * sin(θ_ij))
    q_from = -br.b_ff * Vi^2 + Vi * Vj * (-br.b_ft * cos(θ_ij) + br.g_ft * sin(θ_ij))
    p_to   = br.g_tt * Vj^2 + Vi * Vj * (br.g_tf * cos(θ_ij) - br.b_tf * sin(θ_ij))
    q_to   = -br.b_tt * Vj^2 + Vi * Vj * (-br.b_tf * cos(θ_ij) - br.g_tf * sin(θ_ij))

    return p_from, q_from, p_to, q_to
end

# --- Rectangular NLP ---
function _branch_flow_expressions(net::ACOPFNetwork, av::ACOPFVariables,
                                   l::Int, t::Int, ::RectNLPFormulation)
    br = net.branches[l]
    i, j = br.from_bus, br.to_bus

    # W-space products in terms of e, f:
    # w_ii = e_i² + f_i²
    # wr_ij = e_i×e_j + f_i×f_j
    # wi_ij = f_i×e_j - e_i×f_j
    ei, fi = av.vr[i, t], av.vi_rect[i, t]
    ej, fj = av.vr[j, t], av.vi_rect[j, t]

    w_ii  = ei^2 + fi^2
    w_jj  = ej^2 + fj^2
    wr_ij = ei * ej + fi * fj
    wi_ij = fi * ej - ei * fj

    p_from = br.g_ff * w_ii  + br.g_ft * wr_ij + br.b_ft * wi_ij
    q_from = -br.b_ff * w_ii - br.b_ft * wr_ij + br.g_ft * wi_ij
    p_to   = br.g_tt * w_jj  + br.g_tf * wr_ij - br.b_tf * wi_ij
    q_to   = -br.b_tt * w_jj - br.b_tf * wr_ij - br.g_tf * wi_ij

    return p_from, q_from, p_to, q_to
end

# =============================================================================
# Power Balance (KCL) — Shared across all formulations
# =============================================================================

"""
    add_acopf_power_balance!(model, net, vars, av, input, formulation;
                             extra_injections_fn=nothing)

Add active and reactive power balance constraints at each bus.
The active KCL mirrors the DC KCL in transmission_dc.jl (same injection terms).
The reactive KCL is new and enforces Q balance with estimated reactive load.
"""
function add_acopf_power_balance!(model::Model, net::ACOPFNetwork,
                                   vars::PowerSystemVariables,
                                   av::ACOPFVariables,
                                   input::PowerSystemInput,
                                   formulation::ACOPFFormulation;
                                   extra_injections_fn = nothing)
    n_bus = net.num_buses
    hours = size(input.demand, 1)
    base_mva = net.base_mva

    # Initialize balance constraint storage
    vars.balance_constraints = Dict{Tuple{Int,Int}, Any}()
    av.q_balance_constraints = Dict{Tuple{Int,Int}, Any}()

    # Reactive load estimation: Q_load = P_load × tan(acos(pf_load))
    pf_load = input.acopf_load_power_factor
    q_factor = tan(acos(clamp(pf_load, 0.1, 1.0)))

    # Reactive power slack variables at each bus (absorbs Q mismatch).
    # In SOC/QC/SDP relaxations, exact Q balance is often infeasible because
    # the relaxed voltage products don't correspond to a physical solution.
    # A small penalty makes the problem feasible while keeping Q close to balanced.
    q_slack_pos = @variable(model, [i=1:n_bus, t=1:hours],
        lower_bound=0, base_name="q_slack_pos")
    q_slack_neg = @variable(model, [i=1:n_bus, t=1:hours],
        lower_bound=0, base_name="q_slack_neg")
    # Penalty: small enough to not distort P dispatch, large enough to discourage abuse
    q_penalty = input.acopf_q_slack_penalty
    for i in 1:n_bus, t in 1:hours
        set_objective_coefficient(model, q_slack_pos[i, t], q_penalty)
        set_objective_coefficient(model, q_slack_neg[i, t], q_penalty)
    end

    for t in 1:hours
        for i in 1:n_bus
            # Node index and demand fraction (used by EV and other node-level vars)
            ni = input.network.bus_to_node[i]
            bus_df = input.network.buses[i].demand_fraction

            # ============================================================
            # Active power injection (identical to DC KCL)
            # ============================================================

            # Generator output
            gen_sum = @expression(model,
                sum(vars.gen_output[g, i, t] for g in vars.gens_at_bus[i];
                    init=AffExpr(0.0)))

            # Battery
            bat_discharge = @expression(model,
                sum(vars.bat_discharge[b, i, t] for b in vars.bats_at_bus[i];
                    init=AffExpr(0.0)))
            bat_charge_sum = @expression(model,
                sum(vars.bat_charge[b, i, t] for b in vars.bats_at_bus[i];
                    init=AffExpr(0.0)))

            # EV (node-level variables scaled by demand_fraction)
            ev_v2g_term = vars.ev_v2g !== nothing ? vars.ev_v2g[ni, t] * bus_df : AffExpr(0.0)
            ev_charging_term = vars.ev_charging !== nothing ? vars.ev_charging[ni, t] * bus_df : AffExpr(0.0)

            # Electrolyzer
            electrolyzer_term = vars.electrolyzer_power !== nothing ?
                vars.electrolyzer_power[i, t] : AffExpr(0.0)

            # ACDC converters
            acdc_term = AffExpr(0.0)
            if vars.acdc_rectify !== nothing && vars.acdc_invert !== nothing
                for (c_idx, conv) in enumerate(input.network.acdc_converters)
                    if conv.from_node == i
                        add_to_expression!(acdc_term, -1.0, vars.acdc_rectify[c_idx, t])
                        add_to_expression!(acdc_term, conv.efficiency_invert, vars.acdc_invert[c_idx, t])
                    elseif conv.to_node == i
                        add_to_expression!(acdc_term, conv.efficiency_rectify, vars.acdc_rectify[c_idx, t])
                        add_to_expression!(acdc_term, -1.0, vars.acdc_invert[c_idx, t])
                    end
                end
            end

            # Frequency converters
            freq_term = AffExpr(0.0)
            if vars.freq_flow_a_to_b !== nothing && vars.freq_flow_b_to_a !== nothing
                for (c_idx, conv) in enumerate(input.network.freq_converters)
                    if conv.from_node == i
                        add_to_expression!(freq_term, -1.0, vars.freq_flow_a_to_b[c_idx, t])
                        add_to_expression!(freq_term, conv.efficiency_b_to_a, vars.freq_flow_b_to_a[c_idx, t])
                    elseif conv.to_node == i
                        add_to_expression!(freq_term, conv.efficiency_a_to_b, vars.freq_flow_a_to_b[c_idx, t])
                        add_to_expression!(freq_term, -1.0, vars.freq_flow_b_to_a[c_idx, t])
                    end
                end
            end

            # Bus demand
            bus_demand = input.demand[t, input.network.bus_to_node[i]] *
                         input.network.buses[i].demand_fraction

            # Reservoir pump
            reservoir_pump_term = AffExpr(0.0)
            if vars.reservoir_pump !== nothing
                for g in vars.gens_at_bus[i]
                    gen = input.generators[g]
                    ni = input.network.bus_to_node[i]
                    if gen.reservoir_capacity[ni] > 0 && gen.reservoir_pump_capacity[ni] > 0
                        add_to_expression!(reservoir_pump_term, vars.reservoir_pump[g, i, t])
                    end
                end
            end

            # Rooftop solar
            rooftop_gen_term = if hasproperty(input, :rooftop_generation) && input.rooftop_generation !== nothing
                input.rooftop_generation[t, ni] * bus_df
            else
                0.0
            end
            rooftop_curt_term = vars.rooftop_curtailment !== nothing ?
                vars.rooftop_curtailment[ni, t] * bus_df : AffExpr(0.0)

            # Extra injections (master problem)
            extra_term = extra_injections_fn !== nothing ?
                extra_injections_fn(i, t) : AffExpr(0.0)

            # Net active injection (same as DC KCL).  load_shed is per-bus (B2).
            net_inj_p = @expression(model,
                gen_sum + bat_discharge + ev_v2g_term + vars.load_shed[i, t] +
                rooftop_gen_term + acdc_term + freq_term + extra_term -
                bus_demand - electrolyzer_term - bat_charge_sum - reservoir_pump_term -
                vars.reserve_static[ni, t] * bus_df - vars.reserve_dynamic[ni, t] * bus_df -
                ev_charging_term - rooftop_curt_term
            )

            # ============================================================
            # Active power flow sum (formulation-specific)
            # ============================================================
            # KCL in MW: net_inj_MW = base_mva × Σ P_flow_pu(branches)
            # Both sides in MW keeps coefficient ratios manageable for
            # conic solvers (avoids the tiny inv_base = 0.01 factor that
            # created 260,000:1 ratios with summed admittances).

            p_terms = Any[]
            q_terms = Any[]
            for (l, is_from) in net.bus_branches[i]
                pf, qf, pt, qt = _branch_flow_expressions(net, av, l, t, formulation)
                if is_from
                    push!(p_terms, pf)
                    push!(q_terms, qf)
                else
                    push!(p_terms, pt)
                    push!(q_terms, qt)
                end
            end

            p_flow_sum = isempty(p_terms) ? AffExpr(0.0) : sum(p_terms)
            q_flow_sum = isempty(q_terms) ? AffExpr(0.0) : sum(q_terms)

            # Active power balance: injection_MW = base_mva × flow_pu
            con_p = @constraint(model, net_inj_p == base_mva * p_flow_sum,
                base_name = "kcl_p_$(i)_$(t)")
            vars.balance_constraints[(i, t)] = con_p

            # ============================================================
            # Reactive power balance (new for ACOPF)
            # ============================================================
            # Also in MW(Ar) to keep well-scaled
            q_load_mvar = bus_demand * q_factor

            # Reactive generation (in MVAr, same units as q_gen bounds)
            q_gen_sum = AffExpr(0.0)
            if av.q_gen !== nothing
                for g in vars.gens_at_bus[i]
                    add_to_expression!(q_gen_sum, 1.0, av.q_gen[(g, i, t)])
                end
            end

            # Reactive converter injection (ACDC converters with Q capability)
            q_conv_term = AffExpr(0.0)
            # (Future extension: add Q variables for converters)

            # Reactive power balance: Q_gen - Q_load + Q_slack = base_mva × Q_flow_pu (all MVAr)
            con_q = @constraint(model,
                q_gen_sum + q_conv_term - q_load_mvar +
                q_slack_pos[i, t] - q_slack_neg[i, t] == base_mva * q_flow_sum,
                base_name = "kcl_q_$(i)_$(t)")
            av.q_balance_constraints[(i, t)] = con_q

            # (Rooftop curtailment limit is now per-node, added below)
        end

        # ── Per-node constraints (outside bus loop) ──
        n_node = input.network.num_nodes
        for ni in 1:n_node
            if vars.rooftop_curtailment !== nothing && hasproperty(input, :rooftop_generation) && input.rooftop_generation !== nothing
                @constraint(model,
                    vars.rooftop_curtailment[ni, t] <= input.rooftop_generation[t, ni],
                    base_name = "rooftop_curt_ub_n$(ni)_$(t)")
            end

        end

        # load_shed caps (per-bus zero for non-demand, per-node total).
        n_bus_ac = input.network.num_buses
        buses_per_node_lc = Dict{Int, Vector{Int}}()
        for b in 1:n_bus_ac
            ni = input.network.bus_to_node[b]
            bus_role = input.network.buses[b].role
            is_load_bus = bus_role == "load" || bus_role == "mixed"
            bus_df = is_load_bus ? input.network.buses[b].demand_fraction : 0.0
            bus_demand_t = input.demand[t, ni] * bus_df
            if bus_demand_t <= 0.0
                @constraint(model,
                    vars.load_shed[b, t] <= 0.0,
                    base_name = "max_load_shed_acopf_b$(b)_t$(t)")
            end
            push!(get!(buses_per_node_lc, ni, Int[]), b)
        end
        for (ni, bs) in buses_per_node_lc
            node_demand_t = input.demand[t, ni]
            if length(bs) > 0 && node_demand_t > 0
                @constraint(model,
                    sum(vars.load_shed[b, t] for b in bs) <= 2.0 * node_demand_t,
                    base_name = "max_load_shed_acopf_node$(ni)_t$(t)")
            end
        end
    end
end

# =============================================================================
# Apparent Power Line Limits
# =============================================================================

"""
    add_acopf_line_limits!(model, net, av, input, formulation)

Add apparent power (MVA) limits on branches: |S_from| ≤ S_max, |S_to| ≤ S_max.

For convex formulations (SOC/QC/SDP): uses SOC constraint [cap; P; Q] ∈ SOC.
For NLP formulations (Polar/Rect): uses nonlinear P² + Q² ≤ cap² (Ipopt
does not support SecondOrderCone).
"""
# Apparent-power thermal limits |S| ≤ S_max picked per formulation:
# * Conic relaxations (SOC/QC/SDP) → JuMP's SecondOrderCone set, which
#   Clarabel/Mosek/Gurobi accept natively.
# * NLP (Polar/Rect) → quadratic P²+Q² ≤ cap², because Ipopt does not
#   implement the SecondOrderCone constraint set even via MOI bridges
#   when the underlying expressions are nonlinear.
function add_acopf_line_limits!(model::Model, net::ACOPFNetwork,
                                 av::ACOPFVariables,
                                 input::PowerSystemInput,
                                 formulation::ACOPFFormulation)
    hours = size(input.demand, 1)
    base_mva = net.base_mva
    is_nlp = formulation isa PolarNLPFormulation || formulation isa RectNLPFormulation

    for (l, br) in enumerate(net.branches)
        cap = br.capacity_mva
        cap <= 0 && continue  # no limit

        cap_pu = cap / base_mva
        cap_pu_sq = cap_pu * cap_pu

        for t in 1:hours
            p_from, q_from, p_to, q_to = _branch_flow_expressions(
                net, av, l, t, formulation)

            if is_nlp
                # Ipopt: nonlinear quadratic constraint
                @constraint(model,
                    p_from^2 + q_from^2 <= cap_pu_sq,
                    base_name = "sline_from_$(l)_$(t)")
                @constraint(model,
                    p_to^2 + q_to^2 <= cap_pu_sq,
                    base_name = "sline_to_$(l)_$(t)")
            else
                # Conic relaxation: SOC set
                @constraint(model,
                    [cap_pu, p_from, q_from] in SecondOrderCone(),
                    base_name = "sline_from_$(l)_$(t)")
                @constraint(model,
                    [cap_pu, p_to, q_to] in SecondOrderCone(),
                    base_name = "sline_to_$(l)_$(t)")
            end
        end
    end
end

# =============================================================================
# Main Entry Point
# =============================================================================

"""
    setup_acopf!(model, vars, input; extra_injections_fn=nothing) → ACOPFVariables

Main entry point: sets up all ACOPF constraints for the selected formulation.
Called from create_power_system() in power_system.jl when power_flow_mode
starts with "acopf_".

Returns ACOPFVariables for result extraction.
"""
function setup_acopf!(model::Model, vars::PowerSystemVariables,
                       input::PowerSystemInput;
                       extra_injections_fn = nothing)
    formulation = parse_acopf_formulation(input.power_flow_mode)
    hours = size(input.demand, 1)

    # Validate solver compatibility
    _validate_solver_for_formulation(model, formulation)

    # Build network data
    net = ACOPFNetwork(input.network, input)

    # Create formulation-specific variables
    av = build_acopf_variables!(model, net, vars, input, formulation)

    # Add converter variables/constraints (shared with DC path)
    add_converter_constraints!(model, vars, input)

    # Add voltage linking constraints
    add_acopf_voltage_constraints!(model, net, av, hours, formulation)

    # Add power balance (KCL) — active + reactive
    add_acopf_power_balance!(model, net, vars, av, input, formulation;
                              extra_injections_fn=extra_injections_fn)

    # Add apparent power line limits
    add_acopf_line_limits!(model, net, av, input, formulation)

    return av
end

# =============================================================================
# Solver Validation
# =============================================================================

function _validate_solver_for_formulation(model, ::Union{SOCFormulation, QCFormulation})
    # SOC/QC require SOCP support — HiGHS 1.5+, Gurobi, CPLEX, Mosek
    # No easy way to check at model creation time; solver will error if unsupported
end

function _validate_solver_for_formulation(model, ::SDPFormulation)
    @info "SDP formulation requires an SDP-capable solver (SCS, Mosek, CSDP). " *
          "Set solver_name accordingly."
end

function _validate_solver_for_formulation(model, ::Union{PolarNLPFormulation, RectNLPFormulation})
    @info "NLP formulation requires a nonlinear solver (Ipopt). " *
          "Set solver_name='ipopt' in the configuration."
end

# =============================================================================
# Solution Extraction Helpers
# =============================================================================

"""
    extract_acopf_voltages(av::ACOPFVariables, n_bus, hours) → (vm, va)

Extract voltage magnitude and angle from the solved ACOPF model.
Returns matrices [bus × hour].
"""
function extract_acopf_voltages(av::ACOPFVariables, n_bus::Int, hours::Int)
    vm_result = ones(n_bus, hours)
    va_result = zeros(n_bus, hours)

    if av.vm !== nothing
        # Polar formulation
        for i in 1:n_bus, t in 1:hours
            vm_result[i, t] = value(av.vm[i, t])
            va_result[i, t] = value(av.va[i, t])
        end
    elseif av.vr !== nothing
        # Rectangular formulation
        for i in 1:n_bus, t in 1:hours
            e = value(av.vr[i, t])
            f = value(av.vi_rect[i, t])
            vm_result[i, t] = sqrt(e^2 + f^2)
            va_result[i, t] = atan(f, e)
        end
    elseif av.w !== nothing
        # W-space formulation (SOC/QC/SDP)
        for i in 1:n_bus, t in 1:hours
            w_val = value(av.w[i, t])
            vm_result[i, t] = sqrt(max(w_val, 0.0))
            # Angle: can be estimated from wr/wi of incident branches
            # but is not uniquely determined in relaxed formulations
            va_result[i, t] = 0.0  # Not available from W-space
        end
    end

    return vm_result, va_result
end

"""
    extract_acopf_reactive_gen(av::ACOPFVariables, n_gen, n_bus, hours) → Array{Float64,3}

Extract reactive generation Q_gen[gen, bus, hour] from solved model.
"""
function extract_acopf_reactive_gen(av::ACOPFVariables,
                                     n_gen::Int, n_bus::Int, hours::Int)
    result = zeros(n_gen, n_bus, hours)
    if av.q_gen !== nothing
        # av.q_gen is a JuMP DenseAxisArray declared as
        #   @variable(model, [idx in q_gen_pairs], ...)
        # where each q_gen_pairs[k] is a Tuple{Int,Int,Int} = (gen, bus, hour).
        # So the single-axis key carries the tuple in key.I[1].
        for key in keys(av.q_gen)
            g, i, t = key.I[1]
            result[g, i, t] = value(av.q_gen[key])
        end
    end
    return result
end
