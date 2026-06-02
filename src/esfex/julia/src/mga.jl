"""
mga.jl - MGA/SPORES (Modeling to Generate Alternatives)

Implements the SPORES algorithm (Lombardi et al. 2020) for generating
diverse near-optimal investment alternatives in capacity expansion planning.

Algorithm:
1. Solve cost-optimal problem → C*, x*₀
2. Add near-optimal constraint: total_cost ≤ (1 + ε) × C*
3. For k = 1..K:
   a. Compute frequency scores from previous solutions
   b. Set diversity-maximizing LP objective
   c. Solve → x*_k
"""

"""
    compute_frequency_scores(
        alternatives, input;
        investment_threshold = 0.1
    )

Compute SPORES frequency scores for each (technology, node, year) combination
based on investment patterns in previous solutions.

For each investment variable:
- `frequency = count(invested > threshold) / num_solutions`
- `score = 1 - 2 × frequency`

Score ranges from +1 (never invested → encourage) to -1 (always invested → discourage).

# Returns
- `Dict{String, Float64}`: Variable key → diversity score
"""
function compute_frequency_scores(
    alternatives::Vector{MasterProblemResult},
    input::MasterProblemInput;
    investment_threshold::Float64 = 0.1
)::Dict{String, Float64}
    if isempty(alternatives)
        return Dict{String, Float64}()
    end

    num_alts = length(alternatives)
    num_years = length(input.years)
    n_buses = input.network.num_buses
    n_tech = length(input.technologies)
    n_bat_tech = length(input.battery_technologies)

    counts = Dict{String, Int}()

    for alt in alternatives
        for y_idx in 1:num_years
            # Technology investments
            if haskey(alt.tech_investment, y_idx)
                for t in 1:n_tech
                    if haskey(alt.tech_investment[y_idx], t)
                        for n in 1:n_buses
                            if alt.tech_investment[y_idx][t][n] > investment_threshold
                                key = "tech_$(t)_$(n)_$(y_idx)"
                                counts[key] = get(counts, key, 0) + 1
                            end
                        end
                    end
                end
            end

            # Battery technology power investments
            if haskey(alt.bat_tech_power_investment, y_idx)
                for bt in 1:n_bat_tech
                    if haskey(alt.bat_tech_power_investment[y_idx], bt)
                        for n in 1:n_buses
                            if alt.bat_tech_power_investment[y_idx][bt][n] > investment_threshold
                                key = "bat_tech_pow_$(bt)_$(n)_$(y_idx)"
                                counts[key] = get(counts, key, 0) + 1
                            end
                        end
                    end
                end
            end

            # Battery technology capacity investments
            if haskey(alt.bat_tech_capacity_investment, y_idx)
                for bt in 1:n_bat_tech
                    if haskey(alt.bat_tech_capacity_investment[y_idx], bt)
                        for n in 1:n_buses
                            if alt.bat_tech_capacity_investment[y_idx][bt][n] > investment_threshold
                                key = "bat_tech_cap_$(bt)_$(n)_$(y_idx)"
                                counts[key] = get(counts, key, 0) + 1
                            end
                        end
                    end
                end
            end

            # Transmission investments
            if haskey(alt.transfer_investment, y_idx)
                for ((i, j), inv_value) in alt.transfer_investment[y_idx]
                    if inv_value > investment_threshold
                        key = "trans_$(i)_$(j)_$(y_idx)"
                        counts[key] = get(counts, key, 0) + 1
                    end
                end
            end
        end
    end

    # Convert counts to scores: score = 1 - 2 × frequency
    scores = Dict{String, Float64}()
    for (key, count) in counts
        frequency = count / num_alts
        scores[key] = 1.0 - 2.0 * frequency
    end

    return scores
end

"""
    set_spores_objective!(model, vars, input, frequency_scores)

Replace the model objective with a diversity-maximizing SPORES objective.

Objective: max Σ score_{g,n,y} × x_{g,n,y} / x_max_{g,n}

Each investment variable is weighted by its frequency score (encouraging
under-represented investments) and normalized by maximum capacity.

The model sense is changed to MAX_SENSE.
"""
function set_spores_objective!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput,
    frequency_scores::Dict{String, Float64}
)
    num_years = length(input.years)
    n_buses = input.network.num_buses
    b2n = input.network.bus_to_node
    n_tech = length(input.technologies)
    n_bat_tech = length(input.battery_technologies)

    diversity_obj = AffExpr(0.0)

    # Technology investments (only at investment period starts)
    ypp = vars.years_per_inv_period
    for y_idx in 1:ypp:num_years
        for t in 1:n_tech
            tech = input.technologies[t]
            for n in 1:n_buses
                x_max = tech.invest_max[n]
                if x_max <= 0
                    continue
                end

                key = "tech_$(t)_$(n)_$(y_idx)"
                # Default score = 1.0 for variables never invested (encourage new)
                score = get(frequency_scores, key, 1.0)
                coeff = score / x_max
                add_to_expression!(diversity_obj, vars.tech_investment[y_idx][t][n], coeff)
            end
        end
    end

    # Battery technology power investments
    for y_idx in 1:ypp:num_years
        for bt in 1:n_bat_tech
            bat_tech = input.battery_technologies[bt]
            for n in 1:n_buses
                x_max = bat_tech.invest_max_power[n]
                if x_max <= 0
                    continue
                end

                key = "bat_tech_pow_$(bt)_$(n)_$(y_idx)"
                score = get(frequency_scores, key, 1.0)
                coeff = score / x_max
                add_to_expression!(diversity_obj, vars.bat_tech_power_investment[y_idx][bt][n], coeff)
            end
        end
    end

    # Battery technology capacity investments
    for y_idx in 1:ypp:num_years
        for bt in 1:n_bat_tech
            bat_tech = input.battery_technologies[bt]
            for n in 1:n_buses
                x_max = bat_tech.invest_max_capacity[n]
                if x_max <= 0
                    continue
                end

                key = "bat_tech_cap_$(bt)_$(n)_$(y_idx)"
                score = get(frequency_scores, key, 1.0)
                coeff = score / x_max
                add_to_expression!(diversity_obj, vars.bat_tech_capacity_investment[y_idx][bt][n], coeff)
            end
        end
    end

    # Transmission investments
    for y_idx in 1:ypp:num_years
        for ((i, j), var) in vars.transfer_investment[y_idx]
            x_max = input.network.transference_invest_max[i]
            if x_max <= 0
                continue
            end

            key = "trans_$(i)_$(j)_$(y_idx)"
            score = get(frequency_scores, key, 1.0)
            coeff = score / x_max
            add_to_expression!(diversity_obj, var, coeff)
        end
    end

    # Replace objective: maximize diversity
    set_objective_function(model, diversity_obj)
    set_objective_sense(model, MAX_SENSE)
end

"""
    run_mga_spores(
        input::MasterProblemInput;
        num_alternatives = 10,
        slack_fraction = 0.05,
        use_representative_days = true,
        investment_threshold = 0.1
    )

Run the MGA/SPORES algorithm to generate diverse near-optimal alternatives.

# Algorithm
1. Solve cost-optimal problem → C*, x*₀
2. Add constraint: cost ≤ (1+ε)×C*
3. For k=1..K: maximize diversity from previous solutions

# Arguments
- `input`: Master problem specification
- `num_alternatives`: Number of diversity alternatives K (excludes cost-optimal)
- `slack_fraction`: Near-optimal slack ε (0.05 = 5% cost increase allowed)
- `use_representative_days`: Use representative days for operations
- `investment_threshold`: MW threshold to count as "invested" for scoring

# Returns
- `MGAResult`: Container with all alternatives (index 1 = cost-optimal)
"""
function run_mga_spores(
    input::MasterProblemInput;
    num_alternatives::Int = 10,
    slack_fraction::Float64 = 0.05,
    use_representative_days::Bool = true,
    investment_threshold::Float64 = 0.1
)::MGAResult

    # =====================================================================
    # STEP 0: Solve cost-optimal problem
    # =====================================================================
    @info "MGA/SPORES: Solving cost-optimal problem..."

    model, vars, targets = create_master_problem(
        input;
        use_representative_days = use_representative_days
    )
    optimize!(model)

    status = termination_status(model)
    if status != MOI.OPTIMAL && status != MOI.LOCALLY_SOLVED
        error("MGA/SPORES: Cost-optimal solve failed: $status")
    end

    optimal_cost = objective_value(model)

    # Save the total cost expression BEFORE changing the objective
    total_cost_expr = objective_function(model)

    # Extract cost-optimal solution
    optimal_solution = extract_master_solution(model, vars, input)

    @info "MGA/SPORES: Optimal cost = $(optimal_cost)"
    @info "MGA/SPORES: Near-optimal bound = $(optimal_cost * (1 + slack_fraction))"

    # Initialize storage
    alternatives = MasterProblemResult[optimal_solution]
    alternative_costs = Float64[optimal_cost]
    diversity_objectives = Float64[]

    # =====================================================================
    # STEP 1: Add near-optimal constraint
    # =====================================================================
    max_cost = optimal_cost * (1.0 + slack_fraction)
    @constraint(model, mga_near_optimal, total_cost_expr <= max_cost)

    @info "MGA/SPORES: Added near-optimal constraint (cost ≤ $(max_cost))"

    # =====================================================================
    # STEP 2: Iterative diversity maximization
    # =====================================================================
    for k in 1:num_alternatives
        @info "MGA/SPORES: Generating alternative $(k)/$(num_alternatives)..."

        # Compute frequency scores from all previous solutions
        freq_scores = compute_frequency_scores(
            alternatives, input;
            investment_threshold = investment_threshold
        )

        # Set diversity-maximizing objective
        set_spores_objective!(model, vars, input, freq_scores)

        # Solve
        optimize!(model)

        status = termination_status(model)
        if status != MOI.OPTIMAL && status != MOI.LOCALLY_SOLVED
            @warn "MGA/SPORES: Alternative $(k) solve failed: $(status). " *
                  "Stopping with $(length(alternatives)) alternatives."
            break
        end

        # Extract diversity objective value
        div_obj = objective_value(model)
        push!(diversity_objectives, div_obj)

        # Evaluate actual cost using the saved cost expression
        alt_cost = value(total_cost_expr)
        push!(alternative_costs, alt_cost)

        # Extract solution (note: .objective field will contain diversity value)
        alt_solution = extract_master_solution(model, vars, input)
        push!(alternatives, alt_solution)

        cost_increase_pct = 100.0 * (alt_cost / optimal_cost - 1.0)
        @info "  Cost: $(alt_cost) ($(round(cost_increase_pct, digits=1))% above optimal)"
        @info "  Diversity objective: $(div_obj)"
    end

    @info "MGA/SPORES: Generated $(length(alternatives)) alternatives (including cost-optimal)"

    return MGAResult(
        alternatives,
        length(alternatives),
        slack_fraction,
        optimal_cost,
        alternative_costs,
        diversity_objectives,
        fill("hsj_diversity", length(diversity_objectives)),
    )
end


# =============================================================================
# SPORES — distinct objectives for spatially-explicit practically-optimal runs
# =============================================================================
#
# Each ``set_*_objective!`` replaces the model's current objective with a new
# linear program suitable for the spatially-explicit SPORES family
# (Lombardi et al. 2020). The cost-cap constraint added by the caller stays in
# place so every alternative is feasible against the same near-optimal envelope.
#
# Auxiliary variables / constraints introduced by these objectives are stashed
# in ``model[_SPORES_AUX_KEY]`` so that successive calls on the same model
# (one per SPORES objective) can clean up the previous batch before installing
# their own — otherwise the model would accumulate dead variables across
# alternatives and the solver would slow down.

const _SPORES_AUX_KEY = :_spores_objective_aux

function _clear_spores_aux!(model::Model)
    od = JuMP.object_dictionary(model)
    if haskey(od, _SPORES_AUX_KEY)
        for ref in od[_SPORES_AUX_KEY]
            try
                JuMP.delete(model, ref)
            catch
                # The ref might have been deleted in a previous pass; the
                # try/catch keeps cleanup idempotent.
            end
        end
        delete!(od, _SPORES_AUX_KEY)
    end
end

function _stash_spores_aux!(model::Model, refs::AbstractVector)
    JuMP.object_dictionary(model)[_SPORES_AUX_KEY] = collect(refs)
end


"""
    set_min_build_objective!(model, vars, input)

Replace the model objective with the SPORES *minimum-total-build* objective:

    min  Σ_{t,n,y} x_invest_{t,n,y}
       + Σ_{bt,n,y} x_bat_pow_{bt,n,y}
       + Σ_{(i,j),y} x_transfer_{(i,j),y}

i.e. the smallest cumulative MW commitment that still satisfies every other
constraint (including the cost-cap added by the caller). Investment-period
gating mirrors ``set_spores_objective!`` so only period-start years count.
"""
function set_min_build_objective!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput,
)
    _clear_spores_aux!(model)

    num_years = length(input.years)
    n_buses = input.network.num_buses
    n_tech = length(input.technologies)
    n_bat_tech = length(input.battery_technologies)
    ypp = vars.years_per_inv_period

    obj = AffExpr(0.0)
    for y_idx in 1:ypp:num_years
        for t in 1:n_tech, n in 1:n_buses
            add_to_expression!(obj, vars.tech_investment[y_idx][t][n], 1.0)
        end
        for bt in 1:n_bat_tech, n in 1:n_buses
            add_to_expression!(obj, vars.bat_tech_power_investment[y_idx][bt][n], 1.0)
        end
        for ((_i, _j), var) in vars.transfer_investment[y_idx]
            add_to_expression!(obj, var, 1.0)
        end
    end

    set_objective_function(model, obj)
    set_objective_sense(model, MIN_SENSE)
end


"""
    set_tech_equity_objective!(model, vars, input)

Replace the model objective with the SPORES *technology equity* objective:
minimise the largest per-technology share of the build, i.e.

    min  M
    s.t. Σ_{n,y} x_invest_{t,n,y} / x_max_{t,n}  ≤  M    for each technology t

The resulting alternative spreads investments as evenly as possible across the
technology portfolio, exposing whether the near-optimal envelope allows a
"every tech contributes" plan or whether a couple of technologies remain
dominant regardless of the slack.
"""
function set_tech_equity_objective!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput,
)
    _clear_spores_aux!(model)

    num_years = length(input.years)
    n_buses = input.network.num_buses
    n_tech = length(input.technologies)
    ypp = vars.years_per_inv_period

    # Anonymous aux variable + per-tech constraints; stash for cleanup.
    M = @variable(model, lower_bound = 0.0)
    aux_refs = Any[M]

    for t in 1:n_tech
        tech = input.technologies[t]
        total = AffExpr(0.0)
        for y_idx in 1:ypp:num_years, n in 1:n_buses
            x_max = tech.invest_max[n]
            if x_max <= 0
                continue
            end
            add_to_expression!(total, vars.tech_investment[y_idx][t][n], 1.0 / x_max)
        end
        # If a tech has no positive invest_max anywhere its `total` is empty
        # → constraint reduces to `0 ≤ M`, which is trivially satisfied.
        c = @constraint(model, total <= M)
        push!(aux_refs, c)
    end

    _stash_spores_aux!(model, aux_refs)
    set_objective_function(model, M)
    set_objective_sense(model, MIN_SENSE)
end


"""
    set_regional_equity_objective!(model, vars, input)

Replace the model objective with the SPORES *regional equity* objective:
minimise the largest per-node share of the build, i.e.

    min  M
    s.t. Σ_{t,y} x_invest_{t,n,y} / x_max_{t,n}
       + Σ_{bt,y} x_bat_pow_{bt,n,y} / x_max_bat_{bt,n}  ≤  M    for each node n

The spatially-explicit twin of :func:`set_tech_equity_objective!` and the
canonical SPORES objective. Pushes investments to under-built nodes when the
cost cap allows it, revealing regional substitution options the cost-optimal
plan hides.
"""
function set_regional_equity_objective!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput,
)
    _clear_spores_aux!(model)

    num_years = length(input.years)
    n_buses = input.network.num_buses
    n_tech = length(input.technologies)
    n_bat_tech = length(input.battery_technologies)
    ypp = vars.years_per_inv_period

    M = @variable(model, lower_bound = 0.0)
    aux_refs = Any[M]

    for n in 1:n_buses
        total = AffExpr(0.0)
        for t in 1:n_tech
            tech = input.technologies[t]
            x_max = tech.invest_max[n]
            if x_max <= 0
                continue
            end
            for y_idx in 1:ypp:num_years
                add_to_expression!(total, vars.tech_investment[y_idx][t][n], 1.0 / x_max)
            end
        end
        for bt in 1:n_bat_tech
            bat_tech = input.battery_technologies[bt]
            x_max = bat_tech.invest_max_power[n]
            if x_max <= 0
                continue
            end
            for y_idx in 1:ypp:num_years
                add_to_expression!(total, vars.bat_tech_power_investment[y_idx][bt][n], 1.0 / x_max)
            end
        end
        c = @constraint(model, total <= M)
        push!(aux_refs, c)
    end

    _stash_spores_aux!(model, aux_refs)
    set_objective_function(model, M)
    set_objective_sense(model, MIN_SENSE)
end


"""
    set_evolutionary_distance_objective!(model, vars, input, reference_solution)

Replace the model objective with the SPORES *evolutionary distance* objective:
maximise the L1 distance (normalised) from a reference solution
``x_ref`` (typically the cost-optimal plan) across every investment variable:

    max  Σ_{t,n,y} |x_invest_{t,n,y} - x_ref_{t,n,y}| / x_max_{t,n}
       + Σ_{bt,n,y} |x_bat_pow_{bt,n,y} - x_ref_bat_{bt,n,y}| / x_max_bat_{bt,n}

The L1 norm is linearised via auxiliary positive / negative deviation
variables ``d⁺``, ``d⁻`` so the model stays an LP. Useful as the
"maximally different feasible plan" in cases where the cost cap admits
several visually similar near-optima.
"""
function set_evolutionary_distance_objective!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput,
    reference_solution::MasterProblemResult,
)
    _clear_spores_aux!(model)

    num_years = length(input.years)
    n_buses = input.network.num_buses
    n_tech = length(input.technologies)
    n_bat_tech = length(input.battery_technologies)
    ypp = vars.years_per_inv_period

    obj = AffExpr(0.0)
    aux_refs = Any[]

    # Helper closure: install ``v - ref = d_pos - d_neg`` and contribute
    # ``coeff × (d_pos + d_neg)`` to the objective.
    function _add_abs!(v::VariableRef, ref_value::Float64, coeff::Float64)
        d_pos = @variable(model, lower_bound = 0.0)
        d_neg = @variable(model, lower_bound = 0.0)
        push!(aux_refs, d_pos); push!(aux_refs, d_neg)
        c = @constraint(model, v - ref_value == d_pos - d_neg)
        push!(aux_refs, c)
        add_to_expression!(obj, d_pos, coeff)
        add_to_expression!(obj, d_neg, coeff)
    end

    # Technology investments
    for y_idx in 1:ypp:num_years
        for t in 1:n_tech
            tech = input.technologies[t]
            ref_y = get(reference_solution.tech_investment, y_idx, nothing)
            for n in 1:n_buses
                x_max = tech.invest_max[n]
                if x_max <= 0
                    continue
                end
                ref_val = ref_y === nothing ? 0.0 :
                          (haskey(ref_y, t) ? ref_y[t][n] : 0.0)
                _add_abs!(vars.tech_investment[y_idx][t][n], ref_val, 1.0 / x_max)
            end
        end
    end

    # Battery power investments
    for y_idx in 1:ypp:num_years
        for bt in 1:n_bat_tech
            bat_tech = input.battery_technologies[bt]
            ref_y = get(reference_solution.bat_tech_power_investment, y_idx, nothing)
            for n in 1:n_buses
                x_max = bat_tech.invest_max_power[n]
                if x_max <= 0
                    continue
                end
                ref_val = ref_y === nothing ? 0.0 :
                          (haskey(ref_y, bt) ? ref_y[bt][n] : 0.0)
                _add_abs!(vars.bat_tech_power_investment[y_idx][bt][n], ref_val, 1.0 / x_max)
            end
        end
    end

    _stash_spores_aux!(model, aux_refs)
    set_objective_function(model, obj)
    set_objective_sense(model, MAX_SENSE)
end


"""
    apply_spores_objective!(model, vars, input, objective::Symbol;
                            frequency_scores = nothing,
                            reference_solution = nothing)

Dispatcher that routes a SPORES objective symbol to the matching
``set_*_objective!`` function. Each call cleans up auxiliary variables
the previous objective installed (see :func:`_clear_spores_aux!`) so the
model can be reused across an entire SPORES sweep without bloat.

The Symbol values mirror :class:`SporesObjective` in the Python schema:
``:hsj_diversity``, ``:min_total_build``, ``:max_tech_equity``,
``:max_regional_equity``, ``:evolutionary_dist``.
"""
function apply_spores_objective!(
    model::Model,
    vars::MasterProblemVariables,
    input::MasterProblemInput,
    objective::Symbol;
    frequency_scores::Union{Nothing, Dict{String, Float64}} = nothing,
    reference_solution::Union{Nothing, MasterProblemResult} = nothing,
)
    if objective === :hsj_diversity
        frequency_scores === nothing &&
            error("apply_spores_objective!: :hsj_diversity requires frequency_scores")
        set_spores_objective!(model, vars, input, frequency_scores)
    elseif objective === :min_total_build
        set_min_build_objective!(model, vars, input)
    elseif objective === :max_tech_equity
        set_tech_equity_objective!(model, vars, input)
    elseif objective === :max_regional_equity
        set_regional_equity_objective!(model, vars, input)
    elseif objective === :evolutionary_dist
        reference_solution === nothing &&
            error("apply_spores_objective!: :evolutionary_dist requires reference_solution")
        set_evolutionary_distance_objective!(model, vars, input, reference_solution)
    else
        error("apply_spores_objective!: unknown SPORES objective :$(objective). " *
              "Valid values: :hsj_diversity, :min_total_build, :max_tech_equity, " *
              ":max_regional_equity, :evolutionary_dist")
    end
end


"""
    run_spores(
        input;
        objectives = [:min_total_build, :max_tech_equity, :max_regional_equity, :evolutionary_dist],
        slack_fraction = 0.05,
        use_representative_days = true,
        investment_threshold = 0.1,
    )

Run a SPORES sweep: solve the cost-optimal plan, install the cost-cap
constraint at ``(1 + ε) × C*``, then solve one alternative per objective
listed in ``objectives``.

Returns an :class:`MGAResult` whose ``alternatives[1]`` is the cost-optimal
plan and whose ``alternatives[2:end]`` are tagged in
``objective_labels`` with the SPORES objective that produced each one.
"""
function run_spores(
    input::MasterProblemInput;
    objectives::Vector{Symbol} = Symbol[
        :min_total_build, :max_tech_equity,
        :max_regional_equity, :evolutionary_dist,
    ],
    slack_fraction::Float64 = 0.05,
    use_representative_days::Bool = true,
    investment_threshold::Float64 = 0.1,
)::MGAResult
    @info "SPORES: Solving cost-optimal problem..."
    model, vars, _ = create_master_problem(
        input;
        use_representative_days = use_representative_days,
    )
    optimize!(model)
    status = termination_status(model)
    if status != MOI.OPTIMAL && status != MOI.LOCALLY_SOLVED
        error("SPORES: Cost-optimal solve failed: $status")
    end

    optimal_cost = objective_value(model)
    total_cost_expr = objective_function(model)
    optimal_solution = extract_master_solution(model, vars, input)

    @info "SPORES: Optimal cost = $(optimal_cost)"
    @info "SPORES: Near-optimal bound = $(optimal_cost * (1 + slack_fraction))"

    alternatives = MasterProblemResult[optimal_solution]
    alternative_costs = Float64[optimal_cost]
    objective_values = Float64[]
    objective_labels = String[]

    max_cost = optimal_cost * (1.0 + slack_fraction)
    @constraint(model, spores_near_optimal, total_cost_expr <= max_cost)
    @info "SPORES: Added near-optimal constraint (cost ≤ $(max_cost))"

    for (k, obj_sym) in enumerate(objectives)
        @info "SPORES: Solving alternative $(k)/$(length(objectives)) — objective :$(obj_sym)"

        # HSJ within a SPORES sweep treats earlier SPORES alternatives as
        # priors; the other objectives ignore the history. This keeps the
        # dispatcher uniform and lets a SPORES sweep include HSJ as one of
        # its objectives when the user wants the classical mix.
        freq_scores = obj_sym === :hsj_diversity ?
            compute_frequency_scores(
                alternatives, input;
                investment_threshold = investment_threshold,
            ) : nothing

        apply_spores_objective!(
            model, vars, input, obj_sym;
            frequency_scores = freq_scores,
            reference_solution = optimal_solution,
        )
        optimize!(model)

        status = termination_status(model)
        if status != MOI.OPTIMAL && status != MOI.LOCALLY_SOLVED
            @warn "SPORES: Objective :$(obj_sym) solve failed: $(status). Skipping."
            continue
        end

        obj_val = objective_value(model)
        alt_cost = value(total_cost_expr)
        alt_solution = extract_master_solution(model, vars, input)

        push!(alternatives, alt_solution)
        push!(alternative_costs, alt_cost)
        push!(objective_values, obj_val)
        push!(objective_labels, String(obj_sym))

        cost_increase_pct = 100.0 * (alt_cost / optimal_cost - 1.0)
        @info "  Cost: $(alt_cost) ($(round(cost_increase_pct, digits=1))% above optimal)"
        @info "  Objective value: $(obj_val)"
    end

    @info "SPORES: Generated $(length(alternatives)) alternatives (including cost-optimal)"

    return MGAResult(
        alternatives,
        length(alternatives),
        slack_fraction,
        optimal_cost,
        alternative_costs,
        objective_values,
        objective_labels,
    )
end
