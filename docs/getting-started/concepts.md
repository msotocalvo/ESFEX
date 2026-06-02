# Core Concepts


## Capacity Expansion Planning

The **Master Problem** minimizes the net present value (NPV) of total system cost over a multi-year planning horizon [**[24]**](../reference/bibliography.md#ref24), choosing which generators and batteries to invest in, which transmission lines to expand, and when. It considers all years simultaneously — a decision to invest in year 3 accounts for the remaining useful life and operational savings through year 25.

The Master Problem is a linear program (LP) operating on representative days [**[22]**](../reference/bibliography.md#ref22), [**[23]**](../reference/bibliography.md#ref23) rather than full hourly detail. Investment decisions are continuous (MW of capacity), avoiding the computational cost of mixed-integer programming at the strategic level. Investment costs are annualized and discounted to present value; the optimizer balances capital cost against operational savings.

---


## Operational Dispatch

Operational dispatch determines the output of every generator and battery in every hour to meet demand at minimum cost while respecting technical constraints. Two modes are available:

- **Economic dispatch** (`development` mode) -- A pure linear program (LP) where all generators operate continuously between zero and rated power. There are no binary on/off decisions, no minimum up/down times, and no start-up costs. This mode is fast to solve and suitable for long-term planning studies where hourly operational detail is secondary to investment decisions.

- **Unit commitment** (`unit_commitment` mode) [**[31]**](../reference/bibliography.md#ref31) -- A mixed-integer program (MIP) with binary generator on/off decisions, minimum up/down time constraints, start-up costs, and minimum stable generation levels. This mode provides more realistic operational detail but is significantly slower to solve, especially for large systems or long planning horizons.

For initial exploration and parameter tuning, `development` mode is recommended. Switch to `unit_commitment` for final studies where operational realism matters.


---


## Two-Stage Decomposition

ESFEX solves the planning problem in two stages [**[25]**](../reference/bibliography.md#ref25), [**[26]**](../reference/bibliography.md#ref26).

```
                    STAGE 1: Master Problem
                    =======================
     All years simultaneously (e.g., 25 years)
     Representative days per year (e.g., 5 days)
     Decision: How much to invest in each technology per year

                           |
                           | Investment schedule
                           | (MW per technology per year)
                           v

                    STAGE 2: Operational Dispatch
                    =============================
     Year by year, sequentially (year 1, then year 2, ...)
     Full hourly detail (8760 hours per year)
     Rolling horizon windows (e.g., 48h with 6h overlap)
     Decision: How much each generator produces each hour

                           |
                           | Hourly dispatch, prices, emissions
                           v

                    Results & Metrics
                    =================
     LCOE, VALLCOE, capacity factors, CO2, load shedding
     HDF5 export with full time series
```

**Stage 1 (Master Problem)** solves over all years simultaneously, determining investment and retirement decisions using representative days to estimate operational costs. Because it sees the entire planning horizon, it can make forward-looking decisions — investing in battery storage in year 5 in anticipation of curtailment problems in year 8.

**Stage 2 (Operational Dispatch)** takes the investment decisions from Stage 1 and solves detailed hourly dispatch for each year independently using the full 8760-hour profiles and a rolling horizon. This stage reveals the actual operational cost, curtailment, load shedding, and electricity prices.

The two stages are solved sequentially (not iteratively). The Master Problem's operational cost estimates from representative days may differ from actual Stage 2 costs; for well-configured systems the difference is small.


---


## Rolling Horizon

Each year's 8760 hours are too many for a single optimization problem. ESFEX divides the year into overlapping windows solved sequentially. For example, with a 48-hour window and 6-hour overlap:

1. Solve hours 1--48 (window 1).
2. Keep results for hours 1--42 (the non-overlapping portion).
3. Use the final state at hour 42 (battery SOC, generator status) as initial conditions for window 2.
4. Solve hours 43--90 (window 2, which overlaps hours 43--48 with window 1).
5. Repeat until all 8760 hours are covered.

Key parameters:

- **Window size** (`rolling_horizon_hours`) -- Larger windows capture longer-duration storage dynamics but are slower to solve.
- **Overlap** (`overlap_hours`) -- The overlap provides continuity between windows. Without overlap, boundary artifacts would appear as sharp changes in dispatch at window boundaries.
- **Boundary conditions** -- Battery SOC and generator status (on/off in unit commitment mode) from the end of the non-overlapping portion of one window initialize the next window.

Only results from the non-overlapping portion are kept. The overlap region is discarded and re-solved by the next window with better foresight.


---


## Representative Days

The Master Problem selects a few **representative days** [**[37]**](../reference/bibliography.md#ref37) from the demand profile to estimate annual operational costs, avoiding the need for full 8760-hour dispatch across all years [**[22]**](../reference/bibliography.md#ref22). Days are chosen to capture the range of operating conditions:

- **High-demand days** -- The days with the highest total demand, which stress generation capacity.
- **Minimum separation** -- Days are required to be at least `min_day_separation` days apart, ensuring they span different seasons and weather patterns rather than clustering around a single peak week.

The number of representative days (`representative_days_per_year`, typically 3--10) controls the accuracy/speed trade-off. Five days is a reasonable starting point. The Master Problem scales costs from representative days to approximate the full year; Stage 2 then refines with full hourly detail.


---


## Age-Based Retirement

Units retire deterministically based on age — no binary retirement decisions are introduced, preserving the LP structure.

- **Existing units**: `age = initial_age + (year - 1)`. The unit is active while `age < lifetime`.
- **New investments**: `age = year - investment_year`. The unit is active while `age < lifetime`.

Example: a diesel generator with `initial_age: 10` and `lifetime: 30` retires in year 21 of the simulation (age = 30). An investment made in year 5 with `lifetime: 25` retires in year 30.

Capacity degrades over time: `effective_capacity = rated_power * (1 - degradation_rate)^age`. Typical degradation rates: 0.3--0.5%/year for renewables, zero for thermal units (maintenance assumed to restore full capacity).


---


## Penalties and Soft Constraints

Several constraints are enforced through penalties in the objective function rather than as hard bounds. This guarantees feasibility even for overconstrained systems. The penalties represent the economic cost of violating each constraint.

| Constraint | Config parameter | Interpretation |
|-----------|-----------------|----------------|
| Unserved demand (loss of load) | `LOSS_DEMAND_TRHESHOLD` | Value of lost load [**[33]**](../reference/bibliography.md#ref33) -- the economic damage of not serving demand |
| RE penetration shortfall | `fre_penalty` | Policy cost of missing the renewable energy target |
| Reserve shortfall | `reserve_penalty` | Cost of not maintaining adequate spinning reserve |
| Curtailment | `curtailment_penalty` | Cost to discourage unnecessary renewable waste |
| CO₂ budget violation | `co2_budget_violation_penalty` | Carbon price or penalty for exceeding the emissions budget |

All penalty values are user-configurable. Higher penalties push the optimizer harder to satisfy the constraint; lower values allow more violation when it is cost-effective. Setting penalties too high can cause numerical issues. See the [Configuration Reference](../reference/config-reference.md) for defaults.

Any violated constraint in the optimal solution has a clear economic interpretation: satisfying the constraint costs more than the penalty.


---


## Renewable Energy Targets

RE targets interpolate linearly from `initial_re_penetration` to `target_re_penetration` over the planning horizon. Example: with initial 0.20 and target 0.80 over 25 years, the year 10 target is approximately 44%.

Annual increment bounds limit year-to-year RE growth, reflecting construction timelines and grid integration constraints.

RE penetration is defined as:

```
RE penetration = total renewable generation / total demand served
```

Battery discharge from energy originally produced by renewable sources counts toward renewable generation. Curtailed renewable energy does not count, since it was not used.

---


## Curtailment

Curtailment is available renewable energy that goes unused because demand is already met or the network cannot absorb it. Rather than relying on cost penalties alone, ESFEX limits curtailment to a fraction of total RE generation through a hard constraint:

```
total curtailment <= max_curtailment_ratio * total renewable generation
```

The default `max_curtailment_ratio` is 0.05 (5%). This means the optimizer cannot waste more than 5% of available renewable energy. When the system approaches this limit, the optimizer is forced to invest in battery storage or other flexibility options to absorb excess renewable production.

This constraint-based approach drives storage investment more effectively than cost penalties alone.


---


## Sectoral Demand

Demand can be split into sectors with different criticality levels. Each sector has a name, a fraction of total demand, and a criticality multiplier that scales the load-shedding penalty:

- **Critical sectors** (hospitals, water treatment, essential services) -- Very high criticality multiplier (e.g., 10x). Shedding these loads is extremely expensive, so the optimizer avoids it.
- **Standard sectors** (residential, commercial) -- Normal criticality multiplier (1x). These are shed only when necessary.
- **Flexible sectors** (industrial processes, EV charging, water heating) -- Lower criticality and can participate in demand response. Temporal shifting allows moving load from peak hours to off-peak hours within configurable time bounds.

Sectoral decomposition enables intelligent load-shedding decisions, prioritizing essential services over flexible loads.


---


## Electric Vehicle Integration

The EV module uses an **S-curve adoption model** [**[53]**](../reference/bibliography.md#ref53) to project fleet growth from an initial count to a saturation level.

- **Multi-category vehicles** -- Different vehicle types (passenger cars, buses, trucks) with distinct energy consumption, battery capacity, and driving patterns.
- **Charging profiles** -- Time-of-day charging demand based on driving patterns and charger availability.
- **V2G (Vehicle-to-Grid)** [**[51]**](../reference/bibliography.md#ref51) -- Bidirectional charging that allows EVs to discharge back to the grid during peak demand, effectively acting as distributed battery storage.
- **Demand integration** -- EV charging demand is added to base electrical demand. When V2G optimization is enabled, the optimizer decides when to charge and discharge the fleet.

---


## Multi-System Modeling

ESFEX can model multiple independent power systems connected through inter-system transmission links and fuel routes. Each system is defined separately with its own nodes, generators, batteries, and demand; the `meta_network` section defines interconnections. Useful for island archipelagos, regional grids, or separate utility territories sharing fuel infrastructure.


---


## Primary Energy

The primary energy module models the fuel supply chain from import to consumption:

- **Fuel availability** -- How much fuel can be imported at each node per time period, with costs and capacity limits.
- **Transport** -- Fuel movement between nodes with capacity limits, transport costs, and losses.
- **Storage** -- Fuel inventory at each node with minimum and maximum levels, injection and withdrawal rates.
- **Coupling to generators** -- Each thermal generator consumes fuel at a rate determined by its heat rate and output. If fuel is unavailable at a node, the generator cannot run.

The primary energy module operates at a coarser temporal resolution than electrical dispatch (typically daily or weekly).


---


## NPV Minimization

$$
\min \sum_{y=1}^{Y} \frac{1}{(1+r)^{y-1}} \left[ C^{inv}_y + C^{op}_y + C^{penalty}_y \right]
$$

where:

- $Y$ is the number of planning years
- $r$ is the discount rate (typically 5--10%)
- $C^{inv}_y$ are investment costs in year $y$ (capital expenditure for new generators, batteries, and transmission)
- $C^{op}_y$ are operational costs in year $y$ estimated from representative days (fuel, O&M, start-up costs)
- $C^{penalty}_y$ are soft constraint violation penalties (unserved demand, RE shortfall, CO2 excess)

The discount rate reflects the time value of money: higher rates favor deferring investment; lower rates favor early investment. The choice of discount rate significantly affects the optimal investment schedule, particularly for capital-intensive renewable technologies.


---


## Near-Optimal Alternatives: MGA and SPORES

In power system planning, multiple investment portfolios often achieve nearly the same total cost. The cost-optimal plan is one feasible answer to *what should we build?*, but it is rarely the only sensible answer. **Near-optimal exploration** systematically maps the rest of the practically-relevant solution space. ESFEX implements two distinct methods for this — they share the same cost-slack envelope but differ in *how* alternatives are produced:

### MGA — Modeling to Generate Alternatives

The classical formulation, due to DeCarolis [**[8]**](../reference/bibliography.md#ref8):

1. Solve the cost-optimal master problem $\to$ $C^*$, $x_0^*$.
2. Add the slack constraint $Z \leq (1+\varepsilon) \cdot C^*$.
3. For $k = 1, \ldots, K$: maximise a single **Hop-Skip-Jump** diversity objective that penalises investment variables seen in $\{x_0^*, \ldots, x_{k-1}^*\}$, then solve.

Every alternative optimises the same diversity score $\sigma = 1 - 2 \cdot \mathrm{freq}$; the score history is the only thing that changes between iterations. Configuration: `master_problem.mga.method = "mga"`, `num_alternatives`, `slack_fraction`.

### SPORES — Spatially-explicit Practically Optimal Results

Introduced by Lombardi et al. (2020) [**[7]**](../reference/bibliography.md#ref7) for Calliope. Instead of one diversity objective applied repeatedly, SPORES solves **one alternative per declared objective** under the same cost cap. ESFEX ships four canonical objectives:

| Objective | LP form | Question it answers |
|-----------|---------|---------------------|
| `min_total_build` | $\min \sum I$ | What is the *smallest* near-optimal portfolio? |
| `max_tech_equity` | min-max over per-tech totals | Can the portfolio be technology-diversified? |
| `max_regional_equity` | min-max over per-node totals | Can investments be spatially spread? |
| `evolutionary_dist` | $\max$ L1 distance from $x_0^*$ | What is the *maximally different* feasible plan? |

Configuration: `master_problem.mga.method = "spores"`, `objectives = [...]`. The alternative count equals `len(objectives)`.

### Reading the output

Both methods produce a set of alternative investment portfolios, each near-optimal in cost but differing in technology mix and location. Decisions appearing in most alternatives are **robust** (must-build); those varying across alternatives are **swappable** (the cost slack actually moved them). SPORES additionally tags each alternative with the objective that produced it so charts and post-processing can colour by objective rather than by index.

For the LP formulation of each method, see [Capacity Expansion §15](../formulation/capacity-expansion.md#15-mgaspores-near-optimal-alternative-exploration). For the tutorial walkthrough, see [Near-Optimal Alternatives](../tutorials/mga.md).


---


## How Concepts Relate

```
Configuration (YAML)
    |
    +--> Demand data (Excel/CSV)
    +--> Availability profiles (CSV)
    +--> Generator / battery / network parameters
    |
    v
Master Problem (Stage 1)
    |
    | Uses: representative days, NPV minimization,
    |       age-based retirement, RE targets,
    |       penalty-based soft constraints
    |
    +--> Investment schedule (MW per technology per year)
    |
    v
Operational Dispatch (Stage 2) -- for each year
    |
    | Uses: rolling horizon, full hourly demand,
    |       availability profiles, curtailment limits,
    |       sectoral demand, EV profiles,
    |       primary energy (if enabled)
    |
    +--> Hourly generation, storage, prices, emissions
    |
    v
Results & Metrics
    |
    +--> LCOE, VALLCOE, capacity factors
    +--> Annual cost, RE penetration, CO2 emissions
    +--> HDF5 export for analysis and visualization
    |
    v
Optional: MGA or SPORES
    |
    | MGA   = classical Hop-Skip-Jump loop (one diversity
    |         objective, K iterations under cost slack)
    | SPORES = one alternative per declared objective
    |         (min-build / tech-equity / regional-equity /
    |          evolutionary-dist under the same cost slack)
    |
    +--> Multiple alternative investment portfolios
```

The two-stage decomposition is the organizing principle: the Master Problem makes strategic decisions (what to build), and operational dispatch makes tactical decisions (how to run it). Penalties provide cost signals guiding investment toward meeting operational constraints.
