"""
transmission_ac.jl - AC Power Flow Verification using Newton-Raphson

Implements a full Newton-Raphson AC power flow solver for post-optimization
verification. Used after DC-OPF dispatch to check voltage profiles, reactive
power flows, and compute actual transmission losses.

Only available in Unit Commitment mode as an optional verification step.

Mathematical formulation:
- Y-bus admittance matrix from line impedances (r+jx) and transformer models
- Bus classification: Slack (ref), PV (generators), PQ (loads)
- Newton-Raphson iteration with polar form Jacobian
- Convergence check on max(|ΔP|, |ΔQ|) < tolerance

Reference: Glover, Sarma & Overbye, "Power Systems Analysis & Design" (6th ed.)
"""

# =============================================================================
# AC Power Flow Configuration
# =============================================================================

"""
    ACPowerFlowConfig

Configuration for Newton-Raphson AC power flow verification.
"""
struct ACPowerFlowConfig
    max_iterations::Int
    tolerance::Float64
    base_mva::Float64
    voltage_min_pu::Float64
    voltage_max_pu::Float64
end

function ACPowerFlowConfig(;
    max_iterations::Int = 50,
    tolerance::Float64 = 1e-6,
    base_mva::Float64 = 100.0,
    voltage_min_pu::Float64 = 0.90,
    voltage_max_pu::Float64 = 1.10
)
    return ACPowerFlowConfig(max_iterations, tolerance, base_mva,
                             voltage_min_pu, voltage_max_pu)
end

# =============================================================================
# AC Power Flow Result
# =============================================================================

"""
    ACPowerFlowResult

Results from Newton-Raphson AC power flow verification.
"""
struct ACPowerFlowResult
    converged::Bool
    iterations::Int
    max_mismatch::Float64
    # Per-bus results
    voltage_magnitude::Vector{Float64}  # |V| per bus (p.u.)
    voltage_angle::Vector{Float64}      # θ per bus (radians)
    p_injection::Vector{Float64}        # Active power injection per bus (MW)
    q_injection::Vector{Float64}        # Reactive power injection per bus (MVAr)
    # Per-branch results (lines first, then transformers)
    p_flow_from::Vector{Float64}        # P flow at from-end (MW)
    p_flow_to::Vector{Float64}          # P flow at to-end (MW)
    q_flow_from::Vector{Float64}        # Q flow at from-end (MVAr)
    q_flow_to::Vector{Float64}          # Q flow at to-end (MVAr)
    p_losses::Vector{Float64}           # Active power losses per branch (MW)
    q_losses::Vector{Float64}           # Reactive power losses per branch (MVAr)
    # Summary
    total_p_losses::Float64             # Total active losses (MW)
    total_q_losses::Float64             # Total reactive losses (MVAr)
    # Violations
    voltage_violations::Vector{Tuple{Int,Float64}}          # (bus, V_pu)
    line_overloads::Vector{Tuple{Int,Float64,Float64}}      # (branch, flow_mw, capacity_mw)
end

# =============================================================================
# Y-bus Matrix Construction
# =============================================================================

"""
    build_ybus(lines, transformers, num_buses) -> Matrix{ComplexF64}

Build the bus admittance matrix Y_bus from transmission line and transformer data.

For lines with series impedance z = r + jx and shunt susceptance b_sh:
    y_series = 1 / (r + jx)
    Y_ii += y_series + j⋅b_sh/2
    Y_jj += y_series + j⋅b_sh/2
    Y_ij -= y_series

For transformers with impedance z, tap ratio t:
    y_series = 1 / (r + jx)
    Y_ii += y_series / t²
    Y_jj += y_series
    Y_ij -= y_series / t
    Y_ji -= y_series / t
"""
function build_ybus(lines::Vector{TransmissionLineData},
                    transformers::Vector{TransformerData},
                    num_buses::Int)
    Y = zeros(ComplexF64, num_buses, num_buses)

    # Process transmission lines
    for line in lines
        i, j = line.from_node, line.to_node
        # Effective impedance per circuit (parallel circuits reduce impedance)
        r = line.resistance_pu / line.num_circuits
        x = line.reactance_pu / line.num_circuits
        b_sh = line.susceptance_pu * line.num_circuits

        if abs(x) < 1e-12 && abs(r) < 1e-12
            continue  # Skip zero-impedance lines
        end

        y_series = 1.0 / complex(r, x)

        # Pi-model: series admittance + shunt elements
        Y[i, i] += y_series + im * b_sh / 2
        Y[j, j] += y_series + im * b_sh / 2
        Y[i, j] -= y_series
        Y[j, i] -= y_series
    end

    # Process transformers
    for trafo in transformers
        i, j = trafo.from_node, trafo.to_node
        r = trafo.resistance_pu
        x = trafo.reactance_pu
        t = trafo.tap_ratio

        if abs(x) < 1e-12
            x = trafo.impedance_pu * 0.99  # Fallback: mostly reactive
        end
        if abs(t) < 1e-12
            t = 1.0  # Fallback: nominal tap
        end

        # Normalize extreme tap ratios (see transmission_acopf.jl for rationale)
        if t < 0.5 || t > 2.0
            t = 1.0
        end

        y_series = 1.0 / complex(r, x)

        # Transformer equivalent circuit with tap ratio
        Y[i, i] += y_series / (t * t)
        Y[j, j] += y_series
        Y[i, j] -= y_series / t
        Y[j, i] -= y_series / t
    end

    return Y
end

# =============================================================================
# Bus Classification
# =============================================================================

"""
    classify_buses(gen_output_t, num_buses, generators, slack_bus) -> Vector{Int}

Classify buses for AC power flow:
- Type 3 (Slack): Reference bus — known |V| and θ
- Type 2 (PV): Generator buses with active non-renewable generation — known P and |V|
- Type 1 (PQ): Load buses — known P and Q
"""
function classify_buses(gen_output_t::Matrix{Float64},
                        num_buses::Int,
                        generators::Vector{GeneratorConfig},
                        slack_bus::Int)
    bus_types = fill(1, num_buses)  # Default: PQ

    n_gen = length(generators)
    for g in 1:n_gen
        if generators[g].type != "Renewable"
            for b in 1:num_buses
                if gen_output_t[g, b] > 1e-3
                    bus_types[b] = 2  # PV (dispatchable generator)
                end
            end
        end
    end

    # Slack bus overrides
    bus_types[slack_bus] = 3

    return bus_types
end

# =============================================================================
# Newton-Raphson Solver
# =============================================================================

"""
    solve_ac_power_flow(Y_bus, P_scheduled, Q_scheduled, bus_types,
                        V_init, theta_init, config) -> (converged, V, θ, iters, mismatch)

Solve AC power flow using Newton-Raphson method in polar coordinates.

State vector: x = [θ(non-slack buses); |V|(PQ buses)]
Mismatch: f = [ΔP(non-slack); ΔQ(PQ)]
Jacobian: J = [H N; M L] where
    H = ∂P/∂θ, N = ∂P/∂|V|, M = ∂Q/∂θ, L = ∂Q/∂|V|
"""
function solve_ac_power_flow(Y_bus::Matrix{ComplexF64},
                             P_scheduled::Vector{Float64},
                             Q_scheduled::Vector{Float64},
                             bus_types::Vector{Int},
                             V_init::Vector{Float64},
                             theta_init::Vector{Float64},
                             config::ACPowerFlowConfig)
    n = length(bus_types)
    G = real.(Y_bus)
    B = imag.(Y_bus)

    V = copy(V_init)
    theta = copy(theta_init)

    # Identify bus sets
    pq_buses = findall(t -> t == 1, bus_types)
    pv_buses = findall(t -> t == 2, bus_types)
    non_slack = findall(t -> t != 3, bus_types)

    n_pq = length(pq_buses)
    n_ns = length(non_slack)
    n_state = n_ns + n_pq  # [θ_non_slack; V_pq]

    if n_state == 0
        return true, V, theta, 0, 0.0
    end

    # Index maps for fast lookup
    ns_idx = Dict(bus => k for (k, bus) in enumerate(non_slack))
    pq_idx = Dict(bus => k for (k, bus) in enumerate(pq_buses))

    max_mismatch = Inf

    for iter in 1:config.max_iterations
        # Calculate P and Q at each bus
        P_calc = zeros(n)
        Q_calc = zeros(n)
        for i in 1:n
            for j in 1:n
                theta_ij = theta[i] - theta[j]
                P_calc[i] += V[i] * V[j] * (G[i,j] * cos(theta_ij) + B[i,j] * sin(theta_ij))
                Q_calc[i] += V[i] * V[j] * (G[i,j] * sin(theta_ij) - B[i,j] * cos(theta_ij))
            end
        end

        # Compute mismatches
        dP = P_scheduled[non_slack] .- P_calc[non_slack]
        dQ = Q_scheduled[pq_buses] .- Q_calc[pq_buses]
        mismatch = vcat(dP, dQ)

        max_mismatch = length(mismatch) > 0 ? maximum(abs.(mismatch)) : 0.0

        if max_mismatch < config.tolerance
            return true, V, theta, iter, max_mismatch
        end

        # Build Jacobian matrix J = [H N; M L]
        J = zeros(n_state, n_state)

        # H submatrix: ∂P/∂θ (non_slack × non_slack)
        for (ri, i) in enumerate(non_slack)
            for (ci, j) in enumerate(non_slack)
                if i == j
                    J[ri, ci] = -Q_calc[i] - B[i,i] * V[i]^2
                else
                    theta_ij = theta[i] - theta[j]
                    J[ri, ci] = V[i] * V[j] * (G[i,j] * sin(theta_ij) - B[i,j] * cos(theta_ij))
                end
            end
        end

        # N submatrix: ∂P/∂|V| (non_slack × pq)
        for (ri, i) in enumerate(non_slack)
            for (ci, j) in enumerate(pq_buses)
                col = n_ns + ci
                if i == j
                    J[ri, col] = P_calc[i] / V[i] + G[i,i] * V[i]
                else
                    theta_ij = theta[i] - theta[j]
                    J[ri, col] = V[i] * (G[i,j] * cos(theta_ij) + B[i,j] * sin(theta_ij))
                end
            end
        end

        # M submatrix: ∂Q/∂θ (pq × non_slack)
        for (ri, i) in enumerate(pq_buses)
            row = n_ns + ri
            for (ci, j) in enumerate(non_slack)
                if i == j
                    J[row, ci] = P_calc[i] - G[i,i] * V[i]^2
                else
                    theta_ij = theta[i] - theta[j]
                    J[row, ci] = -V[i] * V[j] * (G[i,j] * cos(theta_ij) + B[i,j] * sin(theta_ij))
                end
            end
        end

        # L submatrix: ∂Q/∂|V| (pq × pq)
        for (ri, i) in enumerate(pq_buses)
            row = n_ns + ri
            for (ci, j) in enumerate(pq_buses)
                col = n_ns + ci
                if i == j
                    J[row, col] = Q_calc[i] / V[i] - B[i,i] * V[i]
                else
                    theta_ij = theta[i] - theta[j]
                    J[row, col] = V[i] * (G[i,j] * sin(theta_ij) - B[i,j] * cos(theta_ij))
                end
            end
        end

        # Solve linear system: Δx = J \ Δf
        dx = J \ mismatch

        # Update state variables
        theta[non_slack] .+= dx[1:n_ns]
        V[pq_buses] .+= dx[n_ns+1:end]
    end

    # Did not converge
    return false, V, theta, config.max_iterations, max_mismatch
end

# =============================================================================
# Line Flow Calculation
# =============================================================================

"""
    calculate_line_flows(V, theta, lines, transformers, base_mva)
        -> (p_from, p_to, q_from, q_to, p_losses, q_losses)

Calculate active and reactive power flows on each branch after NR convergence.
Results in MW/MVAr (converted from p.u. using base_mva).

Branch ordering: transmission lines first, then transformers.
"""
function calculate_line_flows(V::Vector{Float64}, theta::Vector{Float64},
                              lines::Vector{TransmissionLineData},
                              transformers::Vector{TransformerData},
                              base_mva::Float64)
    n_branches = length(lines) + length(transformers)
    p_from = zeros(n_branches)
    p_to = zeros(n_branches)
    q_from = zeros(n_branches)
    q_to = zeros(n_branches)

    # Transmission lines (standard π-model)
    for (k, line) in enumerate(lines)
        i, j = line.from_node, line.to_node
        r = line.resistance_pu / line.num_circuits
        x = line.reactance_pu / line.num_circuits
        b_sh = line.susceptance_pu * line.num_circuits

        if abs(x) < 1e-12 && abs(r) < 1e-12
            continue
        end

        y = 1.0 / complex(r, x)
        g_s, b_s = real(y), imag(y)

        Vi, Vj = V[i], V[j]
        theta_ij = theta[i] - theta[j]
        cos_ij = cos(theta_ij)
        sin_ij = sin(theta_ij)

        # Power at from-end (bus i → bus j)
        p_from[k] = Vi^2 * g_s - Vi * Vj * (g_s * cos_ij + b_s * sin_ij)
        q_from[k] = -Vi^2 * (b_s + b_sh / 2) + Vi * Vj * (b_s * cos_ij - g_s * sin_ij)

        # Power at to-end (bus j ← bus i)
        p_to[k] = Vj^2 * g_s - Vi * Vj * (g_s * cos_ij - b_s * sin_ij)
        q_to[k] = -Vj^2 * (b_s + b_sh / 2) + Vi * Vj * (b_s * cos_ij + g_s * sin_ij)
    end

    # Transformers (with tap ratio model)
    offset = length(lines)
    for (k, trafo) in enumerate(transformers)
        idx = offset + k
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

        y = 1.0 / complex(r, x)
        g_s, b_s = real(y), imag(y)

        Vi, Vj = V[i], V[j]
        theta_ij = theta[i] - theta[j]
        cos_ij = cos(theta_ij)
        sin_ij = sin(theta_ij)

        # Transformer π-equivalent with tap on from-side
        p_from[idx] = (Vi / t)^2 * g_s - (Vi * Vj / t) * (g_s * cos_ij + b_s * sin_ij)
        q_from[idx] = -(Vi / t)^2 * b_s + (Vi * Vj / t) * (b_s * cos_ij - g_s * sin_ij)
        p_to[idx] = Vj^2 * g_s - (Vi * Vj / t) * (g_s * cos_ij - b_s * sin_ij)
        q_to[idx] = -Vj^2 * b_s + (Vi * Vj / t) * (b_s * cos_ij + g_s * sin_ij)
    end

    # Convert from p.u. to MW/MVAr
    p_from .*= base_mva
    p_to .*= base_mva
    q_from .*= base_mva
    q_to .*= base_mva

    # Losses = P_from + P_to (positive = losses)
    p_losses = p_from .+ p_to
    q_losses = q_from .+ q_to

    return p_from, p_to, q_from, q_to, p_losses, q_losses
end

# =============================================================================
# Main Entry Point
# =============================================================================

"""
    run_ac_power_flow(input::PowerSystemInput, result::PowerSystemResult,
                      config::ACPowerFlowConfig, hour::Int) -> ACPowerFlowResult

Run AC power flow verification for a single hour using the DC-OPF dispatch solution.

Extracts bus injections from the optimization result, builds Y-bus from the
explicit network data, and runs Newton-Raphson to determine actual voltages,
reactive power, and losses.
"""
function run_ac_power_flow(input::PowerSystemInput, result::PowerSystemResult,
                           config::ACPowerFlowConfig, hour::Int)
    n_bus = input.network.num_buses
    n = n_bus  # alias used by the Newton-Raphson state vectors below
    base_mva = config.base_mva
    lines = input.network.transmission_lines
    trafos = input.network.transformers

    # Build Y-bus
    Y = build_ybus(lines, trafos, n_bus)

    # Extract scheduled active power injections from optimization result
    P_gen = zeros(n_bus)
    for g in 1:length(input.generators)
        for i in 1:n_bus
            P_gen[i] += result.gen_output[g, i, hour]
        end
    end

    # Include battery discharge - charge
    for b in 1:length(input.batteries)
        for i in 1:n_bus
            P_gen[i] += result.bat_discharge[b, i, hour]
            P_gen[i] -= result.bat_charge[b, i, hour]
        end
    end

    P_load = zeros(n_bus)
    for i in 1:n_bus
        P_load[i] = input.demand[hour, i]
    end

    # Net injection in p.u.
    P_scheduled = (P_gen .- P_load) ./ base_mva

    # Reactive power: assume power factor 0.9 lagging for loads
    pf_load = 0.9
    Q_load = P_load .* tan(acos(pf_load))
    Q_scheduled = -Q_load ./ base_mva  # Negative = consuming reactive power

    # Classify buses
    gen_at_t = result.gen_output[:, :, hour]
    bus_types = classify_buses(gen_at_t, n, input.generators, input.network.slack_bus)

    # Initial guess: flat start or use DC angles
    V_init = ones(n)
    theta_init = zeros(n)
    # Use DC voltage angles as better initial guess if available
    if size(result.voltage_angle, 2) >= hour
        theta_init = result.voltage_angle[:, hour]
    end

    # Set PV bus voltages to 1.0 p.u. (no better estimate from DC)
    for i in 1:n
        if bus_types[i] == 2 || bus_types[i] == 3
            V_init[i] = 1.0
        end
    end

    # Solve Newton-Raphson
    converged, V, theta, iters, max_mismatch =
        solve_ac_power_flow(Y, P_scheduled, Q_scheduled, bus_types,
                           V_init, theta_init, config)

    # Calculate line flows
    p_from, p_to, q_from, q_to, p_losses, q_losses =
        calculate_line_flows(V, theta, lines, trafos, base_mva)

    # Compute bus injections from converged solution
    G_mat = real.(Y)
    B_mat = imag.(Y)
    P_calc = zeros(n)
    Q_calc = zeros(n)
    for i in 1:n
        for j in 1:n
            theta_ij = theta[i] - theta[j]
            P_calc[i] += V[i] * V[j] * (G_mat[i,j] * cos(theta_ij) + B_mat[i,j] * sin(theta_ij))
            Q_calc[i] += V[i] * V[j] * (G_mat[i,j] * sin(theta_ij) - B_mat[i,j] * cos(theta_ij))
        end
    end
    P_calc .*= base_mva
    Q_calc .*= base_mva

    # Check voltage violations
    voltage_violations = Tuple{Int,Float64}[]
    for i in 1:n
        if V[i] < config.voltage_min_pu || V[i] > config.voltage_max_pu
            push!(voltage_violations, (i, V[i]))
        end
    end

    # Check line overloads
    line_overloads = Tuple{Int,Float64,Float64}[]
    for (k, line) in enumerate(lines)
        flow = max(abs(p_from[k]), abs(p_to[k]))
        cap = line.capacity_mw * line.num_circuits
        if cap > 0 && flow > cap
            push!(line_overloads, (k, flow, cap))
        end
    end
    # Check transformer overloads
    offset = length(lines)
    for (k, trafo) in enumerate(trafos)
        idx = offset + k
        flow = max(abs(p_from[idx]), abs(p_to[idx]))
        cap = trafo.rated_power_mva
        if cap > 0 && flow > cap
            push!(line_overloads, (idx, flow, cap))
        end
    end

    return ACPowerFlowResult(
        converged, iters, max_mismatch,
        V, theta, P_calc, Q_calc,
        p_from, p_to, q_from, q_to, p_losses, q_losses,
        sum(p_losses), sum(q_losses),
        voltage_violations, line_overloads
    )
end

# =============================================================================
# GUI Entry Point — Lightweight AC Power Flow
# =============================================================================

"""
    GuiACPowerFlowInput

Lightweight input struct for AC power flow from the GUI editor.
Accepts flat arrays instead of requiring the full PowerSystemInput/PowerSystemResult
structs used during optimization.  This enables the native NR solver to be called
from the Python GUI without building a full optimization model.

# Fields
- `num_buses::Int`: Number of buses in the network.
- `bus_ids::Vector{String}`: Bus identifiers (same order as all per-bus arrays).
- `bus_voltage_kv::Vector{Float64}`: Nominal voltage per bus (kV).
- `bus_types::Vector{Int}`: 1 = PQ, 2 = PV, 3 = Slack.
- `p_gen_mw::Vector{Float64}`: Active generation injection per bus (MW).
- `q_gen_mvar::Vector{Float64}`: Reactive generation injection per bus (MVAr).
- `p_load_mw::Vector{Float64}`: Active load per bus (MW).
- `q_load_mvar::Vector{Float64}`: Reactive load per bus (MVAr).
- `line_from::Vector{Int}`: From-bus index (1-based) for each line.
- `line_to::Vector{Int}`: To-bus index (1-based) for each line.
- `line_r_pu::Vector{Float64}`: Series resistance per line (p.u.).
- `line_x_pu::Vector{Float64}`: Series reactance per line (p.u.).
- `line_b_pu::Vector{Float64}`: Shunt susceptance per line (p.u.).
- `line_capacity_mw::Vector{Float64}`: Thermal limit per line (MW).
- `line_ids::Vector{String}`: Line identifiers.
- `trafo_from::Vector{Int}`: From-bus index (1-based) for each transformer.
- `trafo_to::Vector{Int}`: To-bus index (1-based) for each transformer.
- `trafo_r_pu::Vector{Float64}`: Resistance per transformer (p.u.).
- `trafo_x_pu::Vector{Float64}`: Reactance per transformer (p.u.).
- `trafo_tap::Vector{Float64}`: Tap ratio per transformer.
- `trafo_rated_mva::Vector{Float64}`: Rated power per transformer (MVA).
- `trafo_impedance_pu::Vector{Float64}`: Total impedance per transformer (p.u.).
- `trafo_names::Vector{String}`: Transformer identifiers.
"""
struct GuiACPowerFlowInput
    num_buses::Int
    bus_ids::Vector{String}
    bus_voltage_kv::Vector{Float64}
    bus_types::Vector{Int}
    p_gen_mw::Vector{Float64}
    q_gen_mvar::Vector{Float64}
    p_load_mw::Vector{Float64}
    q_load_mvar::Vector{Float64}
    line_from::Vector{Int}
    line_to::Vector{Int}
    line_r_pu::Vector{Float64}
    line_x_pu::Vector{Float64}
    line_b_pu::Vector{Float64}
    line_capacity_mw::Vector{Float64}
    line_ids::Vector{String}
    trafo_from::Vector{Int}
    trafo_to::Vector{Int}
    trafo_r_pu::Vector{Float64}
    trafo_x_pu::Vector{Float64}
    trafo_tap::Vector{Float64}
    trafo_rated_mva::Vector{Float64}
    trafo_impedance_pu::Vector{Float64}
    trafo_names::Vector{String}
end

"""
    solve_gui_ac_power_flow(gui_input::GuiACPowerFlowInput;
                            max_iterations=50, tolerance=1e-6,
                            base_mva=100.0, voltage_min_pu=0.90,
                            voltage_max_pu=1.10) -> ACPowerFlowResult

Run Newton-Raphson AC power flow from flat GUI arrays.

Converts `GuiACPowerFlowInput` into the internal `TransmissionLineData` /
`TransformerData` structs, builds Y-bus, and calls the existing NR solver.
Returns the standard `ACPowerFlowResult`.
"""
function solve_gui_ac_power_flow(gui::GuiACPowerFlowInput;
                                  max_iterations::Int = 50,
                                  tolerance::Float64 = 1e-6,
                                  base_mva::Float64 = 100.0,
                                  voltage_min_pu::Float64 = 0.90,
                                  voltage_max_pu::Float64 = 1.10)
    n = gui.num_buses
    config = ACPowerFlowConfig(
        max_iterations=max_iterations,
        tolerance=tolerance,
        base_mva=base_mva,
        voltage_min_pu=voltage_min_pu,
        voltage_max_pu=voltage_max_pu,
    )

    # ── Build TransmissionLineData vector ──
    n_lines = length(gui.line_from)
    lines = Vector{TransmissionLineData}(undef, n_lines)
    for k in 1:n_lines
        lines[k] = TransmissionLineData(
            gui.line_ids[k],
            gui.line_from[k], gui.line_to[k],
            gui.line_capacity_mw[k],
            gui.line_x_pu[k],       # reactance
            gui.line_r_pu[k],       # resistance
            gui.line_b_pu[k],       # susceptance
            1.0,                    # length_km (not used in NR)
            220.0,                  # voltage_kv (placeholder)
            1,                      # num_circuits
            50.0,                   # frequency_hz
            "AC",                   # current_type
        )
    end

    # ── Build TransformerData vector ──
    n_trafo = length(gui.trafo_from)
    trafos = Vector{TransformerData}(undef, n_trafo)
    for k in 1:n_trafo
        trafos[k] = TransformerData(
            gui.trafo_names[k],
            gui.trafo_from[k], gui.trafo_to[k],
            0.0, 0.0,                       # from/to voltage (not used in NR)
            gui.trafo_rated_mva[k],
            gui.trafo_impedance_pu[k],
            gui.trafo_r_pu[k],
            gui.trafo_x_pu[k],
            gui.trafo_tap[k],
            0.0,                             # losses_fraction (not used in NR)
        )
    end

    # ── Build Y-bus ──
    Y = build_ybus(lines, trafos, n)

    # ── Net scheduled injections in p.u. ──
    P_scheduled = (gui.p_gen_mw .- gui.p_load_mw) ./ base_mva
    Q_scheduled = (gui.q_gen_mvar .- gui.q_load_mvar) ./ base_mva

    # ── Initial guess: flat start ──
    V_init = ones(n)
    theta_init = zeros(n)
    for i in 1:n
        if gui.bus_types[i] >= 2  # PV or Slack
            V_init[i] = 1.0
        end
    end

    # ── Solve NR ──
    converged, V, theta, iters, max_mismatch =
        solve_ac_power_flow(Y, P_scheduled, Q_scheduled, gui.bus_types,
                           V_init, theta_init, config)

    # ── Line flows ──
    p_from, p_to, q_from, q_to, p_losses, q_losses =
        calculate_line_flows(V, theta, lines, trafos, base_mva)

    # ── Bus injections from converged solution ──
    G_mat = real.(Y)
    B_mat = imag.(Y)
    P_calc = zeros(n)
    Q_calc = zeros(n)
    for i in 1:n
        for j in 1:n
            theta_ij = theta[i] - theta[j]
            P_calc[i] += V[i] * V[j] * (G_mat[i,j] * cos(theta_ij) + B_mat[i,j] * sin(theta_ij))
            Q_calc[i] += V[i] * V[j] * (G_mat[i,j] * sin(theta_ij) - B_mat[i,j] * cos(theta_ij))
        end
    end
    P_calc .*= base_mva
    Q_calc .*= base_mva

    # ── Voltage violations ──
    voltage_violations = Tuple{Int,Float64}[]
    for i in 1:n
        if V[i] < config.voltage_min_pu || V[i] > config.voltage_max_pu
            push!(voltage_violations, (i, V[i]))
        end
    end

    # ── Line overloads ──
    line_overloads = Tuple{Int,Float64,Float64}[]
    for k in 1:n_lines
        flow = max(abs(p_from[k]), abs(p_to[k]))
        cap = gui.line_capacity_mw[k]
        if cap > 0 && flow > cap
            push!(line_overloads, (k, flow, cap))
        end
    end
    # Transformer overloads
    offset = n_lines
    for k in 1:n_trafo
        idx = offset + k
        flow = max(abs(p_from[idx]), abs(p_to[idx]))
        cap = gui.trafo_rated_mva[k]
        if cap > 0 && flow > cap
            push!(line_overloads, (idx, flow, cap))
        end
    end

    return ACPowerFlowResult(
        converged, iters, max_mismatch,
        V, theta, P_calc, Q_calc,
        p_from, p_to, q_from, q_to, p_losses, q_losses,
        sum(p_losses), sum(q_losses),
        voltage_violations, line_overloads
    )
end
