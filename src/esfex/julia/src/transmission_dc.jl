"""
transmission_dc.jl - DC Power Flow using Kirchhoff Formulation

Implements DC power flow constraints based on PyPSA methodology:
- KCL (Kirchhoff's Current Law): bus-level power balance
- KVL (Kirchhoff's Voltage Law): loop flow constraints
- Sparse cycle-based formulation for efficiency
- Per-line impedance data with parallel line support
- Transformer branches with series reactance

Buses are the electrical abstraction; nodes are geographic. Each bus maps to
a parent node via bus_to_node, and carries a demand_fraction of that node's demand.

Reference: "Linear Optimal Power Flow Using Cycle Flows" (Hörsch et al., 2018)
"""

"""
DC Power Flow transmission model using Kirchhoff formulation.

Fields indexed per physical line (supports parallel lines between same bus pair).
Buses are the electrical indexing unit; nodes are geographic.
"""
struct TransmissionDC
    num_buses::Int
    lines::Vector{Tuple{Int, Int}}      # (from_bus, to_bus) per physical line, 1-indexed
    line_reactances::Vector{Float64}    # Per physical line (indexed by position)
    line_capacities::Vector{Float64}    # Per physical line capacity (MW)
    incidence_matrix::Matrix{Float64}   # buses × lines
    max_angle_diff_rad::Float64
    slack_bus::Int
    line_losses::Vector{Float64}        # Per-line loss factor (0-1), legacy linear model
    pwl_losses::Union{PWLLossSegments, Nothing}  # PWL loss model (nothing = use linear)
end

"""
    TransmissionDC(network::NetworkConfig) -> TransmissionDC

Construct TransmissionDC from network configuration.
Uses per-line data if available, otherwise falls back to adjacency matrix.
Transformer branches are appended as additional lines.
All indexing is per bus (electrical), not per node (geographic).
"""
function TransmissionDC(network::NetworkConfig)
    if !isempty(network.transmission_lines)
        # Enhanced mode: per-line data with individual impedances
        raw_lines, raw_capacities, raw_reactances = build_from_line_data(network)
    else
        # Legacy mode: adjacency matrix with uniform reactance
        raw_lines, raw_capacities, raw_reactances = build_from_adjacency(network)
    end

    # Track which raw transmission lines are valid (non-self-loop)
    # Also track per-line loss data source indices
    lines = Tuple{Int, Int}[]
    capacities = Float64[]
    reactances = Float64[]
    transmission_src_indices = Int[]  # index into network.transmission_lines or 0 for adjacency

    for (idx, (i, j)) in enumerate(raw_lines)
        if i == j
            continue
        end
        push!(lines, (i, j))
        push!(capacities, raw_capacities[idx])
        push!(reactances, raw_reactances[idx])
        push!(transmission_src_indices, idx)
    end

    n_transmission = length(lines)

    # Append transformer branches (from_node/to_node are bus indices)
    # Skip self-loop transformers (both ends at the same bus)
    trafo_indices = Int[]
    for (ti, trafo) in enumerate(network.transformers)
        i, j = trafo.from_node, trafo.to_node
        if i == j
            continue
        end
        edge = i < j ? (i, j) : (j, i)
        push!(lines, edge)
        push!(reactances, trafo.reactance_pu)
        push!(capacities, trafo.rated_power_mva)
        push!(trafo_indices, ti)
    end

    K = build_incidence_matrix(network.num_buses, lines)

    # Compute per-line loss factors
    losses = Float64[]
    for (idx, (i, j)) in enumerate(lines)
        if idx <= n_transmission
            # Transmission line
            src = transmission_src_indices[idx]
            if !isempty(network.transmission_lines) && src <= length(network.transmission_lines)
                push!(losses, network.transmission_lines[src].resistance_pu)
            else
                push!(losses, reactances[idx] * network.default_r_to_x_ratio)
            end
        else
            # Transformer branch: use actual series resistance (not losses_fraction)
            # resistance_pu is the proper R for conductance g = R/(R²+X²)
            trafo_idx = trafo_indices[idx - n_transmission]
            push!(losses, network.transformers[trafo_idx].resistance_pu)
        end
    end

    return TransmissionDC(
        network.num_buses,
        lines,
        reactances,
        capacities,
        K,
        network.max_angle_diff_rad,
        network.slack_bus,
        losses,
        nothing  # pwl_losses: computed by compute_pwl_loss_segments() after construction
    )
end

"""
    compute_pwl_loss_segments(transmission::TransmissionDC, num_segments::Int, s_base::Float64) -> PWLLossSegments

Pre-compute piecewise linear loss segments for all transmission lines.

Approximates quadratic losses `P_loss(f) = g_l × f²_pu × S_base` using `N` linear
segments with increasing marginal slopes. Since power flow `pf` is in MW, we convert
to per-unit: `f_pu = pf / S_base`, giving `P_loss = g_l × pf² / S_base`.
Convexity ensures the LP solver fills lower-cost segments first without binary variables.

For each line `l` with conductance `g_l = R/(R²+X²)` and capacity `f_max` (MW):
- Breakpoints: `f_k = k × f_max / N` for `k = 0, 1, ..., N`
- Segment width: `Δf = f_max / N`
- Slope of segment k: `m_k = g_l × (2k - 1) × Δf / S_base`

Lines with zero resistance or zero capacity get zero slopes (lossless).
"""
function compute_pwl_loss_segments(transmission::TransmissionDC, num_segments::Int, s_base::Float64=100.0)
    if s_base <= 0
        s_base = 100.0
    end
    n_lines = length(transmission.lines)
    widths = Vector{Vector{Float64}}(undef, n_lines)
    slopes = Vector{Vector{Float64}}(undef, n_lines)
    conductances = zeros(n_lines)

    # Defensive clamp on the loss coefficient.  The DC loss coefficient is
    # R_pu (per-unit series resistance); a physical branch has R_pu well
    # under 1.0 p.u.  MAX_G guards against pathological R_pu from bad
    # configs so PWL slopes stay within a solver-friendly range.
    MAX_G = 50.0

    for l in 1:n_lines
        R_l = l <= length(transmission.line_losses) ? transmission.line_losses[l] : 0.0
        f_max = transmission.line_capacities[l]

        if R_l <= 0 || f_max <= 0
            # Lossless line: zero slopes
            conductances[l] = 0.0
            widths[l] = fill(f_max > 0 ? f_max / num_segments : 1.0, num_segments)
            slopes[l] = zeros(num_segments)
            continue
        end

        # DC power-flow series loss: P_loss_pu ≈ R_pu · P_flow_pu², so the
        # loss coefficient is the per-unit SERIES RESISTANCE R_pu — NOT the
        # admittance conductance G = R/(R²+X²).  Using G overstated losses
        # by ≈1/X² (~400× for a typical 0.05 p.u. branch): the PWL slope
        # g_l·(2k-1)·Δf/S_base reached ~13 MW-loss per MW-flow, making
        # power routing infeasible and forcing ~100% load shed.
        # MAX_G is kept as a defensive clamp against pathological R_pu from
        # bad configs (a physical branch has R_pu well under 1.0 p.u.).
        g_l = min(R_l, MAX_G)
        conductances[l] = g_l

        Δf = f_max / num_segments
        widths[l] = fill(Δf, num_segments)
        # PWL slope in MW/MW: m_k = g_l × (2k-1) × Δf / S_base
        # The /S_base converts from per-unit loss to MW loss for MW-valued flows.
        slopes[l] = [g_l * (2k - 1) * Δf / s_base for k in 1:num_segments]
    end

    return PWLLossSegments(num_segments, widths, slopes, conductances)
end

"""
    build_from_line_data(network::NetworkConfig) -> (lines, capacities, reactances)

Build line list from explicit TransmissionLineData.
Each physical line gets its own entry (supports parallel lines between same node pair).
"""
function build_from_line_data(network::NetworkConfig)
    lines = Tuple{Int, Int}[]
    capacities = Float64[]
    reactances = Float64[]

    for tl in network.transmission_lines
        i, j = tl.from_node, tl.to_node
        edge = i < j ? (i, j) : (j, i)
        push!(lines, edge)
        # Effective values accounting for parallel circuits
        push!(reactances, tl.reactance_pu / tl.num_circuits)
        push!(capacities, tl.capacity_mw * tl.num_circuits)
    end

    return lines, capacities, reactances
end

"""
    build_from_adjacency(network::NetworkConfig) -> (lines, capacities, reactances)

Legacy mode: build line list from adjacency matrix with uniform reactance.
X_ij = (reactance_per_km × distance_km) / base_impedance
Adjacency matrix is now bus×bus.
"""
function build_from_adjacency(network::NetworkConfig)
    lines = Tuple{Int, Int}[]
    capacities = Float64[]
    reactances = Float64[]
    n = network.num_buses

    for i in 1:n
        for j in (i+1):n
            cap_ij = network.connections[i, j]
            cap_ji = network.connections[j, i]
            if cap_ij > 0 || cap_ji > 0
                push!(lines, (i, j))
                push!(capacities, max(cap_ij, cap_ji))
                distance_km = network.distances[i, j]
                x_pu = network.reactance_per_km * distance_km / network.base_impedance
                push!(reactances, x_pu)
            end
        end
    end

    return lines, capacities, reactances
end

"""
    build_incidence_matrix(num_buses, lines) -> Matrix{Float64}

Build bus-line incidence matrix K.
K[i, ℓ] = +1 if line ℓ leaves bus i
K[i, ℓ] = -1 if line ℓ enters bus i
"""
function build_incidence_matrix(num_buses::Int, lines::Vector{Tuple{Int,Int}})
    K = zeros(num_buses, length(lines))

    for (ℓ, (i, j)) in enumerate(lines)
        K[i, ℓ] = +1.0  # Flow leaving bus i
        K[j, ℓ] = -1.0  # Flow entering bus j
    end

    return K
end

"""
    add_dc_constraints!(model, transmission::TransmissionDC, vars, input)

Add DC power flow constraints to JuMP model.
- KCL at each bus (power balance with transmission flows)
- KVL for each independent cycle (Kirchhoff's Voltage Law)
- Slack bus angle reference
- Voltage angle limits (optional)

Power flow variables are created per physical line (supports parallel lines).
Bus demand is derived from the parent node's demand scaled by demand_fraction.
"""
function add_dc_constraints!(model, transmission::TransmissionDC,
                             vars::PowerSystemVariables, input;
                             extra_injections_fn::Union{Nothing, Function} = nothing)
    n_bus = transmission.num_buses
    n_line = length(transmission.lines)
    hours = input.temporal.hours
    slack = transmission.slack_bus

    # Create power flow variables per physical line
    pf_by_line = Vector{Vector{VariableRef}}(undef, n_line)
    for (ℓ, (i, j)) in enumerate(transmission.lines)
        pf_by_line[ℓ] = @variable(model, [1:hours], base_name="pf_$(ℓ)_$(i)_$(j)")
    end
    vars.power_flow_by_line = pf_by_line

    # Also populate the node-pair power_flow dict (consumed by N-1 constraints
    # and result extraction). For parallel lines the first line's variables
    # represent the pair; the full per-line breakdown stays in power_flow_by_line.
    for (ℓ, (i, j)) in enumerate(transmission.lines)
        if !haskey(vars.power_flow, (i, j))
            vars.power_flow[(i, j)] = pf_by_line[ℓ]
        end
    end

    n_gen = length(input.generators)
    n_bat = length(input.batteries)
    n_node = input.network.num_nodes
    b2n = input.network.bus_to_node

    # PWL loss model: pre-allocate storage for per-line per-timestep loss variables
    has_pwl = transmission.pwl_losses !== nothing
    pwl = transmission.pwl_losses  # may be nothing
    pwl_loss_by_line = has_pwl ?
        [Vector{VariableRef}(undef, hours) for _ in 1:n_line] : nothing

    # Detect connected components to handle disconnected networks.
    # Each connected component needs its own angle reference (slack bus).
    # Buses with NO lines are isolated → fix their angle to 0.
    component_id = zeros(Int, n_bus)  # 0 = unvisited
    component_count = 0
    adj = [Set{Int}() for _ in 1:n_bus]
    for (i, j) in transmission.lines
        push!(adj[i], j)
        push!(adj[j], i)
    end
    for start_bus in 1:n_bus
        component_id[start_bus] != 0 && continue
        component_count += 1
        # BFS
        queue = [start_bus]
        component_id[start_bus] = component_count
        head = 1
        while head <= length(queue)
            u = queue[head]; head += 1
            for v in adj[u]
                if component_id[v] == 0
                    component_id[v] = component_count
                    push!(queue, v)
                end
            end
        end
    end

    # For each component, pick a reference bus (slack): use global slack
    # for its component, first bus for all others.
    slack_component = component_id[slack]
    component_slack = Dict{Int, Int}()
    for b in 1:n_bus
        c = component_id[b]
        if !haskey(component_slack, c)
            component_slack[c] = b
        end
    end
    # Override: global slack is the reference for its component
    component_slack[slack_component] = slack

    if component_count > 1
        # @debug not @warn: this function runs at every rolling-horizon
        # window setup. A multi-component network is a property of the
        # topology, so the message would otherwise repeat identically
        # 60+ windows × 25 years and drown the basic console. The
        # per-component angle-reference fixup is deliberate and safe;
        # the topology audit warns once at load time anyway.
        @debug "Disconnected network: $(component_count) components detected. " *
               "Adding per-component angle references."
    end
    for t in 1:hours
        # 1. Slack bus angle reference (one per connected component)
        for (_, ref_bus) in component_slack
            @constraint(model, vars.voltage_angle[ref_bus, t] == 0)
        end

        # PWL loss: create direction decomposition + segment variables for each line
        if has_pwl
            N = pwl.num_segments
            for ℓ in 1:n_line
                g_l = pwl.conductances[ℓ]
                cap_ℓ = transmission.line_capacities[ℓ]
                if g_l <= 0 || cap_ℓ < 0.1  # lossless or negligible capacity
                    # Lossless line: loss = 0
                    ploss = @variable(model, lower_bound=0, upper_bound=0,
                        base_name="ploss_$(ℓ)_$(t)")
                    pwl_loss_by_line[ℓ][t] = ploss
                    continue
                end

                # Direction decomposition: pf = f_pos - f_neg
                f_pos = @variable(model, lower_bound=0,
                    base_name="fpos_$(ℓ)_$(t)")
                f_neg = @variable(model, lower_bound=0,
                    base_name="fneg_$(ℓ)_$(t)")
                @constraint(model, pf_by_line[ℓ][t] == f_pos - f_neg)

                # Incremental segment variables with PWL loss accumulation
                loss_expr = AffExpr(0.0)
                fpos_sum = AffExpr(0.0)
                fneg_sum = AffExpr(0.0)
                for k in 1:N
                    w = pwl.segment_widths[ℓ][k]
                    m = pwl.slopes[ℓ][k]
                    dp = @variable(model, lower_bound=0, upper_bound=w,
                        base_name="dp_$(ℓ)_$(k)_$(t)")
                    dn = @variable(model, lower_bound=0, upper_bound=w,
                        base_name="dn_$(ℓ)_$(k)_$(t)")
                    add_to_expression!(fpos_sum, dp)
                    add_to_expression!(fneg_sum, dn)
                    add_to_expression!(loss_expr, m, dp)
                    add_to_expression!(loss_expr, m, dn)
                end
                @constraint(model, f_pos == fpos_sum)
                @constraint(model, f_neg == fneg_sum)

                # Store loss variable for KCL half-loss split
                ploss = @variable(model, lower_bound=0,
                    base_name="ploss_$(ℓ)_$(t)")
                @constraint(model, ploss == loss_expr)
                pwl_loss_by_line[ℓ][t] = ploss
            end
        end

        # 2. KCL at each bus
        for i in 1:n_bus
            # Node index and bus role (drives how demand-side terms are handled)
            ni = b2n[i]
            bus_role = input.network.buses[i].role
            is_load_bus = bus_role == "load" || bus_role == "mixed"
            # Connection buses carry no demand: their KCL is pure flow balance
            # (gen ± bat ± converters + extra = Σ flows). All node-level demand
            # variables (load_shed, reserves, EV, rooftop) are skipped because
            # they are scaled by demand_fraction which is 0 for connection buses.
            bus_df = is_load_bus ? input.network.buses[i].demand_fraction : 0.0

            # Generation at bus (sparse: only generators with capacity at this bus)
            gen_sum = @expression(model,
                sum(vars.gen_output[g, i, t] for g in vars.gens_at_bus[i]; init=AffExpr(0.0))
            )

            # Battery discharge and charge separately (sparse: only batteries at this bus)
            bat_discharge = @expression(model,
                sum(vars.bat_discharge[b, i, t] for b in vars.bats_at_bus[i]; init=AffExpr(0.0))
            )

            bat_charge_sum = @expression(model,
                sum(vars.bat_charge[b, i, t] for b in vars.bats_at_bus[i]; init=AffExpr(0.0))
            )

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
                vars.electrolyzer_power[i, t]
            else
                AffExpr(0.0)
            end

            # AC/DC converter injection at bus i (from_node/to_node are bus indices)
            acdc_term = AffExpr(0.0)
            if vars.acdc_rectify !== nothing && vars.acdc_invert !== nothing
                for (c_idx, conv) in enumerate(input.network.acdc_converters)
                    if conv.from_node == i  # AC side: loses rectified, gains inverted
                        add_to_expression!(acdc_term,
                            -1.0, vars.acdc_rectify[c_idx, t])
                        add_to_expression!(acdc_term,
                            conv.efficiency_invert, vars.acdc_invert[c_idx, t])
                    elseif conv.to_node == i  # DC side: gains rectified, loses inverted
                        add_to_expression!(acdc_term,
                            conv.efficiency_rectify, vars.acdc_rectify[c_idx, t])
                        add_to_expression!(acdc_term,
                            -1.0, vars.acdc_invert[c_idx, t])
                    end
                end
            end

            # Frequency converter injection at bus i (from_node/to_node are bus indices)
            freq_term = AffExpr(0.0)
            if vars.freq_flow_a_to_b !== nothing && vars.freq_flow_b_to_a !== nothing
                for (c_idx, conv) in enumerate(input.network.freq_converters)
                    if conv.from_node == i  # A side: loses a→b, gains b→a
                        add_to_expression!(freq_term,
                            -1.0, vars.freq_flow_a_to_b[c_idx, t])
                        add_to_expression!(freq_term,
                            conv.efficiency_b_to_a, vars.freq_flow_b_to_a[c_idx, t])
                    elseif conv.to_node == i  # B side: gains a→b, loses b→a
                        add_to_expression!(freq_term,
                            conv.efficiency_a_to_b, vars.freq_flow_a_to_b[c_idx, t])
                        add_to_expression!(freq_term,
                            -1.0, vars.freq_flow_b_to_a[c_idx, t])
                    end
                end
            end

            # Bus demand from parent node scaled by demand_fraction.
            # Connection buses (bus_df = 0) contribute zero to the demand term.
            bus_demand = input.demand[t, input.network.bus_to_node[i]] * bus_df

            # NOTE: flex_curt is NOT in the KCL.  Flexible demand curtailment
            # participates only in the objective (benefit) and in sectoral
            # constraints.  Including it in the KCL allowed the model to
            # eliminate demand at negative cost, preventing fossil dispatch.

            # Reservoir pump power (demand side — pumping consumes electricity)
            reservoir_pump_term = AffExpr(0.0)
            if vars.reservoir_pump !== nothing
                for g in vars.gens_at_bus[i]
                    gen = input.generators[g]
                    nidx = b2n[i]
                    if gen.reservoir_capacity[nidx] > 0 && gen.reservoir_pump_capacity[nidx] > 0
                        add_to_expression!(reservoir_pump_term, vars.reservoir_pump[g, i, t])
                    end
                end
            end

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
                extra_injections_fn(i, t)
            else
                AffExpr(0.0)
            end

            # Net injection: LHS - RHS
            # LHS: gen + bat_discharge + EV_V2G + load_shed + rooftop_gen + converter_injections + extra
            # RHS: bus_demand + electrolyzer + bat_charge + reservoir_pump + reserves + EV_charging + rooftop_curt
            # NOTE: flex_curt is NOT in KCL (only in objective + sectoral constraints)
            # Node-level variables (load_shed, reserves) scaled by demand_fraction for bus-level KCL
            net_inj = @expression(model,
                gen_sum + bat_discharge + ev_v2g_term + vars.load_shed[i, t] +
                rooftop_gen_term + acdc_term + freq_term + extra_term -
                bus_demand - electrolyzer_term - bat_charge_sum - reservoir_pump_term -
                vars.reserve_static[ni, t] * bus_df - vars.reserve_dynamic[ni, t] * bus_df -
                ev_charging_term - rooftop_curt_term
            )

            # Sum of flows using incidence matrix (per physical line)
            # Loss models:
            #   PWL: net_inj = Σ_l (K[i,l] × pf[l,t] - 0.5 × |K[i,l]| × loss[l,t])
            #   Linear (legacy): incoming flows reduced by constant factor
            flow_sum = AffExpr(0.0)
            for ℓ in 1:n_line
                coeff = transmission.incidence_matrix[i, ℓ]
                if abs(coeff) < 1e-10
                    continue
                end

                if has_pwl
                    # PWL model: flow + half-loss allocated at each endpoint
                    # Sign: +0.5 because losses INCREASE power extracted from bus
                    # (bus must supply midpoint flow + its half of losses)
                    # net_inj_i = Σ_l [K[i,l]*pf + 0.5*|K[i,l]|*ploss]
                    add_to_expression!(flow_sum, coeff, pf_by_line[ℓ][t])
                    add_to_expression!(flow_sum, 0.5 * abs(coeff), pwl_loss_by_line[ℓ][t])
                else
                    # Legacy linear model: incoming flow reduced by loss factor
                    loss_factor = ℓ <= length(transmission.line_losses) ? transmission.line_losses[ℓ] : 0.0
                    if loss_factor > 0 && coeff > 0
                        add_to_expression!(flow_sum, coeff * (1 - loss_factor), pf_by_line[ℓ][t])
                    else
                        add_to_expression!(flow_sum, coeff, pf_by_line[ℓ][t])
                    end
                end
            end

            con = @constraint(model, net_inj == flow_sum)

            # Store constraint reference for dual extraction
            if vars.balance_constraints !== nothing
                vars.balance_constraints[(i, t)] = con
            end

            # (Rooftop curtailment limit and max load shed are now per-node, added below)
        end

        # ── Per-node constraints (outside bus loop) ──
        for ni in 1:n_node
            # Rooftop curtailment cannot exceed available rooftop generation at node
            if vars.rooftop_curtailment !== nothing && hasproperty(input, :rooftop_generation) && input.rooftop_generation !== nothing
                @constraint(model,
                    vars.rooftop_curtailment[ni, t] <= input.rooftop_generation[t, ni],
                    base_name = "rooftop_curt_limit_dc_n$(ni)_t$(t)")
            end

        end

        # Per-bus: load_shed = 0 at non-demand buses.  load_shed represents
        # UNSERVED demand; a bus with no local demand has nothing to leave
        # unserved, so allowing it would turn load_shed into virtual
        # generation.  (Physical constraint — not an outcome cap; the
        # economic upper bound on shedding is the VOLL penalty itself.)
        for b in 1:n_bus
            ni = input.network.bus_to_node[b]
            bus_role = input.network.buses[b].role
            is_load_bus = bus_role == "load" || bus_role == "mixed"
            bus_df = is_load_bus ? input.network.buses[b].demand_fraction : 0.0
            bus_demand_t = input.demand[t, ni] * bus_df
            if bus_demand_t <= 0.0
                @constraint(model,
                    vars.load_shed[b, t] <= 0.0,
                    base_name = "max_load_shed_dc_b$(b)_t$(t)")
            end
        end

        # 3. Flow-angle coupling (standard DCOPF: pf_MW = S_base/x_pu × Δθ)
        # This replaces the cycle-based KVL formulation and correctly handles
        # parallel lines (each line independently linked to bus angles).
        # S_base (base_impedance) converts per-unit susceptance to MW/radian.
        s_base = input.network.base_impedance
        for (ℓ, (i, j)) in enumerate(transmission.lines)
            cap_ℓ = transmission.line_capacities[ℓ]
            # Lines with negligible capacity add constraints without meaningful
            # power transfer and worsen LP conditioning. Fix their flow to 0.
            if cap_ℓ < 0.1  # < 0.1 MW is electrically negligible
                @constraint(model, pf_by_line[ℓ][t] == 0,
                    base_name = "flow_zero_$(ℓ)_$(t)")
                continue
            end
            x_ℓ = transmission.line_reactances[ℓ]
            # Guard against zero/negative reactance (e.g. virtual/copper-plate lines)
            if x_ℓ <= 0.0
                x_ℓ = 0.01  # Default minimum reactance (p.u.)
            end
            b_line = s_base / x_ℓ  # MW/radian (physical susceptance)
            @constraint(model,
                pf_by_line[ℓ][t] == b_line * (vars.voltage_angle[i, t] - vars.voltage_angle[j, t]),
                base_name = "flow_angle_$(ℓ)_$(t)")
        end

        # NOTE: DC voltage-angle-difference limits were removed.  In this DC
        # formulation `pf = b_line·Δθ` with b_line capped (numerical
        # conditioning), so a |Δθ| ≤ max_angle constraint imposes the
        # NON-PHYSICAL bound pf ≤ b_line·max_angle — far below the branch
        # thermal rating — stranding generation and forcing spurious load
        # shed.  DC flows are limited physically by the explicit
        # pf ≤ line_capacity constraints; the ±π reference bound on
        # voltage_angle keeps the formulation well-posed.  Voltage-angle /
        # stability limits remain available and physically correct in the
        # ACOPF formulations (transmission_acopf.jl, gated by
        # max_angle_diff_rad < π).
    end
end

"""
    add_line_capacity_constraints!(model, transmission::TransmissionDC, vars, input)

Add thermal capacity limits for transmission lines (per physical line).
"""
function add_line_capacity_constraints!(model, transmission::TransmissionDC,
                                        vars::PowerSystemVariables, input;
                                        capacity_override::Union{Dict{Int, Any}, Nothing} = nothing)
    hours = input.temporal.hours
    is_dev = hasproperty(input, :mode) ? input.mode == "development" : false
    pf_by_line = vars.power_flow_by_line

    for (ℓ, (i, j)) in enumerate(transmission.lines)
        base_capacity = transmission.line_capacities[ℓ]

        if capacity_override !== nothing && haskey(capacity_override, ℓ)
            # Master: use investment-augmented line capacity (AffExpr)
            total_cap = capacity_override[ℓ]
            for t in 1:hours
                @constraint(model, pf_by_line[ℓ][t] <= total_cap)
                @constraint(model, pf_by_line[ℓ][t] >= -total_cap)
            end
        else
            # Standard operational dispatch path
            # Create investment variable if in development mode
            if is_dev && vars.transfer_investment !== nothing
                if !haskey(vars.transfer_investment, (i, j))
                    vars.transfer_investment[(i, j)] = @variable(model,
                        lower_bound=0, base_name="tf_inv_$(i)_$(j)")
                end
            end

            # B10: Transfer symmetry — inv[i,j] == inv[j,i]
            if is_dev && vars.transfer_investment !== nothing && haskey(vars.transfer_investment, (i, j))
                if haskey(vars.transfer_investment, (j, i))
                    @constraint(model,
                        vars.transfer_investment[(i, j)] == vars.transfer_investment[(j, i)],
                        base_name = "transfer_symmetry_$(i)_$(j)")
                end
            end

            for t in 1:hours
                if is_dev && vars.transfer_investment !== nothing && haskey(vars.transfer_investment, (i, j))
                    total_cap = base_capacity + vars.transfer_investment[(i, j)]
                else
                    total_cap = base_capacity
                end

                # Bidirectional flow limits per physical line
                @constraint(model, pf_by_line[ℓ][t] <= total_cap)
                @constraint(model, pf_by_line[ℓ][t] >= -total_cap)
            end
        end
    end
end

"""
    add_converter_constraints!(model, vars, input)

Add AC/DC converter and frequency converter variables and capacity constraints.
Variables:
- acdc_rectify[c, t] ≥ 0  (AC→DC power for converter c at hour t)
- acdc_invert[c, t] ≥ 0   (DC→AC power for converter c at hour t)
- freq_flow_a_to_b[c, t] ≥ 0  (A→B power for converter c at hour t)
- freq_flow_b_to_a[c, t] ≥ 0  (B→A power for converter c at hour t)

Constraints:
- Capacity: rectify + invert ≤ rated_power (per converter)
- Capacity: flow_a_to_b + flow_b_to_a ≤ rated_power (per converter)

KCL injection is handled in add_dc_constraints! (reads these variables).
"""
function add_converter_constraints!(model, vars::PowerSystemVariables,
                                     input)
    hours = input.temporal.hours
    network = input.network

    # --- AC/DC Converters ---
    n_acdc = length(network.acdc_converters)
    if n_acdc > 0
        acdc_rect = @variable(model, [1:n_acdc, 1:hours], lower_bound=0,
                              base_name="acdc_rectify")
        acdc_inv = @variable(model, [1:n_acdc, 1:hours], lower_bound=0,
                             base_name="acdc_invert")
        vars.acdc_rectify = acdc_rect
        vars.acdc_invert = acdc_inv

        for (c, conv) in enumerate(network.acdc_converters)
            for t in 1:hours
                # Mutual capacity: total through-power ≤ rated
                @constraint(model,
                    acdc_rect[c, t] + acdc_inv[c, t] <= conv.rated_power_mva)
                # Minimum power (if applicable)
                if conv.min_power_mva > 0
                    # Only enforce when active — skip for now (would need binary)
                end
            end
        end
    end

    # --- Frequency Converters ---
    n_freq = length(network.freq_converters)
    if n_freq > 0
        freq_ab = @variable(model, [1:n_freq, 1:hours], lower_bound=0,
                            base_name="freq_a_to_b")
        freq_ba = @variable(model, [1:n_freq, 1:hours], lower_bound=0,
                            base_name="freq_b_to_a")
        vars.freq_flow_a_to_b = freq_ab
        vars.freq_flow_b_to_a = freq_ba

        for (c, conv) in enumerate(network.freq_converters)
            for t in 1:hours
                @constraint(model,
                    freq_ab[c, t] + freq_ba[c, t] <= conv.rated_power_mva)
            end
        end
    end
end

"""
    add_converter_objective_terms(model, vars, input) -> AffExpr

Return objective cost expression for converter operations.
Cost = Σ variable_cost × (rectify + invert) for each converter and hour.
"""
function add_converter_objective_terms(vars::PowerSystemVariables,
                                       input::PowerSystemInput)
    cost = AffExpr(0.0)
    hours = input.temporal.hours
    network = input.network

    # AC/DC converter variable costs
    if vars.acdc_rectify !== nothing && vars.acdc_invert !== nothing
        for (c, conv) in enumerate(network.acdc_converters)
            if conv.variable_cost > 0
                for t in 1:hours
                    add_to_expression!(cost, conv.variable_cost,
                                       vars.acdc_rectify[c, t])
                    add_to_expression!(cost, conv.variable_cost,
                                       vars.acdc_invert[c, t])
                end
            end
            # Standby losses as fixed cost per hour
            if conv.standby_losses_mw > 0
                # Simple linear proxy: add small cost term
                cost += conv.standby_losses_mw * hours * conv.variable_cost
            end
        end
    end

    # Frequency converter variable costs
    if vars.freq_flow_a_to_b !== nothing && vars.freq_flow_b_to_a !== nothing
        for (c, conv) in enumerate(network.freq_converters)
            if conv.variable_cost > 0
                for t in 1:hours
                    add_to_expression!(cost, conv.variable_cost,
                                       vars.freq_flow_a_to_b[c, t])
                    add_to_expression!(cost, conv.variable_cost,
                                       vars.freq_flow_b_to_a[c, t])
                end
            end
            if conv.standby_losses_mw > 0
                cost += conv.standby_losses_mw * hours * conv.variable_cost
            end
        end
    end

    return cost
end
