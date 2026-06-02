# Stochastic Programming

The stochastic extension enables robust capacity expansion planning under uncertainty by considering multiple future scenarios simultaneously. It is implemented in `master_problem.jl` via `create_stochastic_master_problem()`.

---


## 1. Overview


The deterministic Master Problem assumes perfect knowledge of future conditions -- fuel prices, demand growth, technology costs, and renewable resource availability are all known with certainty. In practice, these parameters are subject to significant uncertainty over multi-decade planning horizons. The stochastic formulation [**[9]**](../reference/bibliography.md#ref9), [**[10]**](../reference/bibliography.md#ref10) addresses this by considering multiple **scenarios**, each representing a plausible future, and optimizing the **expected cost** across all scenarios.

The key insight of stochastic programming is the distinction between decisions that must be made before uncertainty is resolved (**here-and-now** decisions) and decisions that can adapt to realized conditions (**wait-and-see** decisions):

- **First-stage (here-and-now)**: Investment decisions \(I^{gen}_{y,g,n}\), \(I^{bat,P}_{y,b,n}\), \(I^{bat,E}_{y,b,n}\), \(I^{tr}_{y,i,j}\) are made once and must be the same across all scenarios. These represent infrastructure commitments that cannot be changed after construction.
- **Second-stage (wait-and-see)**: Operational dispatch decisions can differ by scenario. Each scenario has its own set of representative day dispatch variables reflecting how the system would operate under that particular realization of uncertainty.

---


## 2. Scenario Tree Structure

### 2.1 Scenario Definition


Each scenario \(\omega \in \Omega\) is defined by a probability weight and a set of parameter multipliers:

\[
\Omega = \{(\omega_1, \pi_1), (\omega_2, \pi_2), \ldots, (\omega_S, \pi_S)\} \quad \text{where } \sum_{s=1}^{S} \pi_s = 1
\tag{STOCH-1}
\]

The probability weights \(\pi_s\) represent the decision-maker's belief about the likelihood of each scenario. They must be non-negative and sum to one, forming a valid probability distribution.

### 2.2 Scenario Multipliers


The `ScenarioMultipliers` struct defines multiplicative factors that are applied to the base-case parameters to create each scenario's parameter set. This approach is parsimonious: rather than specifying complete parameter sets for each scenario, only the deviations from the base case are specified.

| Multiplier | Default | Description | Affected Parameters |
|-----------|---------|-------------|---------------------|
| `demand_multiplier` | 1.0 | Scales electrical demand | \(D_{n,t}\) |
| `fuel_cost_multiplier` | 1.0 | Scales all fuel costs | \(c^{fuel}_{g,n}\) |
| `renewable_availability_multiplier` | 1.0 | Scales RE capacity factors | \(\alpha_{g,t,n}\) |
| `investment_cost_multiplier` | 1.0 | Scales generation investment costs | \(c^{inv}_{g,n}\) |
| `battery_cost_multiplier` | 1.0 | Scales storage investment costs | \(c^{inv,P}_{b,n}\), \(c^{inv,E}_{b,n}\) |
| `co2_cost_multiplier` | 1.0 | Scales CO2 emission penalty | \(C^{CO2}\) |
| `discount_rate_multiplier` | 1.0 | Scales discount rate | \(r\) |
| `demand_growth_multiplier` | 1.0 | Scales annual demand growth rate | \(\gamma\) |
| `ev_adoption_multiplier` | 1.0 | Scales EV fleet growth rate | \(r_{EV}\) |
| `technology_improvement_multiplier` | 1.0 | Scales efficiency improvement | Various |

Multipliers are applied by `apply_scenario_multipliers()`, which creates modified copies of the generator and battery configurations for each scenario. A multiplier of 1.0 means no change from the base case; values greater than 1.0 increase the parameter, and values less than 1.0 decrease it.

### 2.3 Scenario Construction


The effective parameter for scenario \(\omega_s\) is computed as:

\[
\theta_s = \theta^{base} \times m_s
\]

where \(\theta^{base}\) is the base-case parameter value and \(m_s\) is the corresponding multiplier. For example, if the base fuel cost is 50 \$/MWh and the fuel cost multiplier for a "high fuel price" scenario is 1.5, the effective fuel cost in that scenario is 75 \$/MWh.

---


## 3. Two-Stage Formulation

### 3.1 Mathematical Structure


The stochastic Master Problem has the following two-stage structure:

**First stage** (deterministic, common to all scenarios):

\[
\min_{I} \; C^{1st}(I) + \mathbb{E}_\omega\left[Q(I, \omega)\right]
\]

subject to investment constraints (INV-1 through INV-3, BUD-1, TXN-1) that are scenario-independent.

**Second stage** (recourse, scenario-dependent):

For each scenario \(\omega_s\), the recourse function \(Q(I, \omega_s)\) is the optimal operational cost given investment decisions \(I\) and scenario parameters \(\omega_s\):

\[
Q(I, \omega_s) = \min_{P,E,L,\ldots} \; C^{op}_s(P, E, L, \ldots)
\]

subject to operational constraints (power balance, generator limits, battery dynamics, etc.) parameterized by scenario \(\omega_s\) and linked to investment decisions through cumulative capacity expressions.

### 3.2 Non-Anticipativity


The non-anticipativity constraint is enforced implicitly: there is only one copy of the first-stage investment variables shared across all scenarios. This is equivalent to the explicit constraint:

\[
I^{gen,(\omega_1)}_{y,g,n} = I^{gen,(\omega_2)}_{y,g,n} = \ldots = I^{gen,(\omega_S)}_{y,g,n} \qquad \forall y, g, n
\]

and similarly for battery and transmission investment variables.

In the JuMP implementation, this is achieved by using a single set of investment variables that appear in the constraints of all scenarios. No additional constraints are needed.

Second-stage variables (representative day operational variables) are scenario-specific, each with its own index:

\[
P^{day,(\omega_s)}_{g,n,t} \text{ varies by scenario } s \quad \text{(separate variable set per scenario)}
\]

---


## 4. Objective Function

### 4.1 Stochastic Objective


The stochastic objective minimizes the sum of deterministic first-stage investment costs and expected second-stage operational costs:

\[
\min \; Z^{stoch} = \underbrace{\sum_{y=1}^{Y} \frac{C^{inv}_y}{(1+r)^{y-1}}}_{\text{First stage (deterministic)}} + \underbrace{\sum_{s=1}^{S} \pi_s \sum_{y=1}^{Y} \frac{C^{op}_{y,s}}{(1+r_s)^{y-1}}}_{\text{Second stage (expected cost)}} + C^{slack}
\tag{STOCH-OBJ}
\]

Built by `build_stochastic_objective!()`.

### 4.2 First-Stage Investment Cost


Investment costs may be modified by scenario-specific multipliers. However, since investment decisions are common across scenarios, the investment cost in the objective uses either the base-case costs (unscaled) or the expected investment cost:

\[
C^{inv}_y = \sum_{g,n} c^{inv}_{g,n} \cdot I^{gen}_{y,g,n} + \sum_{b,n} \left( c^{inv,P}_{b,n} \cdot I^{bat,P}_{y,b,n} + c^{inv,E}_{b,n} \cdot I^{bat,E}_{y,b,n} \right) + \sum_{(i,j)} c^{tr}_i \cdot I^{tr}_{y,i,j}
\]

### 4.3 Second-Stage Operational Cost


For each scenario \(s\), the operational cost is computed from representative day dispatch, scaled to annual:

\[
C^{op}_{y,s} = \frac{365}{|\mathcal{D}_y|} \sum_{d \in \mathcal{D}_y} C^{day}_{y,d,s}
\]

where \(C^{day}_{y,d,s}\) is the daily operational cost under scenario \(s\), computed using the scenario-modified fuel costs, maintenance costs, and penalty coefficients.

### 4.4 Scenario-Adjusted Discount Rate


Each scenario can have a different effective discount rate:

\[
r_s = r^{base} \times m^{discount}_s
\]

where \(m^{discount}_s\) is the `discount_rate_multiplier` for scenario \(s\). This allows modeling scenarios with different capital market conditions or risk premia.

### 4.5 Expected Cost Calculation


The expected total cost is a weighted sum over scenarios:

\[
\mathbb{E}_\omega[Z] = \sum_{s=1}^{S} \pi_s \cdot Z_s
\]

For example, with three scenarios (high demand, base case, green transition) with probabilities (0.3, 0.5, 0.2):

\[
\mathbb{E}[Z] = 0.3 \cdot Z_{high} + 0.5 \cdot Z_{base} + 0.2 \cdot Z_{green}
\]

The optimal investment plan minimizes this expected cost, hedging against all three futures simultaneously.

### 4.6 Slack Penalties


Slack penalties are applied per scenario with probability weighting:

\[
C^{slack} = \sum_{s=1}^{S} \pi_s \sum_{y=1}^{Y} \left[ c^{slack} \cdot s^{re}_{y,s} + c^{slack} \cdot s^{bud}_{y} + \sum_n c^{slack} \cdot s^{cap}_{y,n,s} \right]
\]

The budget slack \(s^{bud}_y\) is scenario-independent (since investment is common), while RE target and capacity adequacy slacks are scenario-dependent.

---


## 5. Constraint Structure

### 5.1 Scenario-Independent Constraints


The following constraints apply identically across all scenarios:

| Constraint Family | Reference | Description |
|-------------------|-----------|-------------|
| INV-1 to INV-3 | [Capacity Expansion](capacity-expansion.md#71-investment-limits-inv) | Cumulative investment limits |
| BUD-1 | [Capacity Expansion](capacity-expansion.md#72-budget-constraint-bud) | Annual investment budget |
| TXN-1 | [Capacity Expansion](capacity-expansion.md#73-transmission-symmetry-txn) | Transmission symmetry |

### 5.2 Scenario-Dependent Constraints


For each scenario \(s\), a separate set of representative day operational constraints is created with scenario-modified parameters:

| Constraint | Modification |
|-----------|-------------|
| Power balance (PB-1) | Demand scaled by `demand_multiplier` and `demand_growth_multiplier` |
| Generator capacity (GEN-1) | RE availability scaled by `renewable_availability_multiplier` |
| Operational cost (OBJ-5) | Fuel costs scaled by `fuel_cost_multiplier` |
| CO2 emissions (CO2-1) | CO2 penalty scaled by `co2_cost_multiplier` |
| RE target (RE-DAY-1) | Target may be adjusted based on scenario parameters |

The cumulative capacity expressions (CUM-1 through CUM-4) link the common investment variables to each scenario's operational constraints, ensuring that the same physical infrastructure serves all scenarios.

---


## 6. Illustrative Example


Consider a three-scenario planning problem for a small island grid:

| Scenario | Probability | Key Assumptions |
|----------|------------|-----------------|
| High Demand | 0.3 | 20% higher demand, 10% higher fuel costs |
| Base Case | 0.5 | No modifications |
| Green Transition | 0.2 | 15% better RE resources, 20% cheaper RE investment, 30% cheaper batteries |

**Configuration:**

```yaml
master_problem:
  stochastic: true

systems:
  island_grid:
    stochastic_scenarios:
      high_demand:
        probability: 0.3
        multipliers:
          demand_multiplier: 1.2
          fuel_cost_multiplier: 1.1

      base_case:
        probability: 0.5
        multipliers:
          demand_multiplier: 1.0

      green_transition:
        probability: 0.2
        multipliers:
          renewable_availability_multiplier: 1.15
          investment_cost_multiplier: 0.8
          battery_cost_multiplier: 0.7
```

**Interpretation:** The optimizer will select investments that perform reasonably well across all three futures. If the "green transition" scenario strongly favors solar + storage, but the "high demand" scenario requires firm capacity, the stochastic solution will include some firm generation capacity that the purely green-transition deterministic solution might omit.

---


## 7. Computational Considerations

### 7.1 Problem Size


The stochastic formulation scales the second-stage problem linearly with the number of scenarios:

| Component | Deterministic | Stochastic (\(S\) scenarios) |
|-----------|--------------|-------------------------------|
| Investment variables | \(V_1\) | \(V_1\) (shared) |
| Operational variables | \(V_2\) | \(S \times V_2\) |
| Investment constraints | \(C_1\) | \(C_1\) (shared) |
| Operational constraints | \(C_2\) | \(S \times C_2\) |
| Total LP size | \(V_1 + V_2\), \(C_1 + C_2\) | \(V_1 + S \cdot V_2\), \(C_1 + S \cdot C_2\) |

For a typical Master Problem with 5 representative days, 25 years, and 3 nodes, the deterministic problem might have ~100,000 variables. With 3 scenarios, the stochastic problem grows to ~250,000 variables (investment variables shared, operational variables tripled).

### 7.2 Solution Time


Solution time scales roughly linearly with the number of scenarios for LP problems (simplex or barrier methods). For practical planning studies:

- **2--3 scenarios**: Minimal overhead, suitable for sensitivity analysis
- **5--10 scenarios**: Moderate computation, captures key uncertainties
- **20+ scenarios**: Significant computation; consider scenario reduction techniques

### 7.3 Representative Day Sharing


All scenarios share the same representative day selection (from the base-case demand profile). This simplifies the implementation and ensures that the same temporal structure is used across scenarios, differing only in parameter values.

When TSAM is enabled (`use_tsam: true`), TSAM periods and weights are computed once from the base case and shared across all scenarios. Only cost and demand multipliers differ between scenarios.

---


## 8. Implementation Notes


!!! warning "Partial Implementation"
    The stochastic framework is structurally complete but has limited testing. For production use, verify scenario results carefully and consider starting with 2--3 scenarios before scaling up.

### 8.1 Implementation Details


- The stochastic Master Problem creates separate representative day variables per scenario via `create_day_operational_vars!()`
- Scenario multipliers are applied to the base input before building each scenario's constraints via `apply_scenario_multipliers()`
- The solver sees a single large LP with shared investment variables and per-scenario operational blocks
- Solution extraction returns per-scenario operational results alongside the common investment plan

### 8.2 Function Call Graph


```
create_stochastic_master_problem(input)
  |-- build_master_variables!(model, input)         # Shared investment variables
  |-- add_investment_constraints!()                  # Shared constraints
  |-- add_budget_constraints!()                      # Shared
  |-- add_transmission_symmetry_constraints!()       # Shared
  |-- for each scenario s:
  |     |-- apply_scenario_multipliers(input, s)     # Create scenario params
  |     |-- add_representative_days_validation!(s)   # Per-scenario dispatch
  |     |-- calculate_target_ratios(input_s)         # Per-scenario RE targets
  |     +-- add_capacity_adequacy_constraints!(s)    # Per-scenario adequacy
  +-- build_stochastic_objective!()                  # Expected cost objective
```

---


## 9. Relationship to Other Formulations


The stochastic extension is compatible with all other ESFEX features:

| Feature | Compatibility | Notes |
|---------|--------------|-------|
| MGA/SPORES | Sequential | Run MGA after stochastic solve to explore near-optimal alternatives |
| DC Power Flow | Full | Per-scenario transmission constraints |
| Primary Energy | Full | Per-scenario fuel supply constraints |
| Multi-System | Full | Per-scenario inter-system coupling |
| TSAM | Full | Shared periods, per-scenario costs |
| NPV Iteration | Full | Applied after stochastic solve |

---

## References

The two-stage stochastic programming formulation follows the framework of Birge and Louveaux [**[9]**](../reference/bibliography.md#ref9). Decision-making under uncertainty in electricity markets is treated by Conejo et al. [**[10]**](../reference/bibliography.md#ref10). The interaction between stochastic capacity expansion and unit commitment constraints is analyzed by Schwele et al. [**[35]**](../reference/bibliography.md#ref35). The EVPI and VSS metrics for quantifying the value of stochastic solutions are defined in Birge and Louveaux [**[9]**](../reference/bibliography.md#ref9) (Ch. 4).

See the [full bibliography](../reference/bibliography.md) for complete citation details.
