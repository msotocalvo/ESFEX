# Master Problem (Capacity Expansion)

The Master Problem is the first stage of ESFEX's two-stage optimization [**[24]**](../reference/bibliography.md#ref24), [**[25]**](../reference/bibliography.md#ref25). It determines **what to build, when to build it, and when to retire it** over the entire planning horizon (typically 10--30 years), minimizing the net present value (NPV) of total system cost [**[26]**](../reference/bibliography.md#ref26). The second stage — operational dispatch — then validates these decisions at full hourly resolution.

The Master Problem answers questions such as:

- How much solar PV, wind, or battery storage capacity should be installed, and in which years?
- When do existing diesel or gas generators retire due to age, and should they be replaced?
- Is transmission expansion between nodes cost-effective?
- What is the cost-optimal trajectory to meet an 80% RE target by 2050?
- How sensitive is the investment plan to fuel price assumptions or technology cost reductions?

```
                          MASTER PROBLEM
                    ┌─────────────────────────┐
                    │  All years simultaneous  │
                    │  Representative days     │
                    │                          │
   INPUTS ─────────►  Minimize NPV of:        ├──────► OUTPUTS
   • Technology     │   Investment costs       │        • MW invested per
     costs          │   + Operational costs    │          technology per year
   • Demand         │   + Penalty costs        │        • Retirement schedule
     projections    │                          │        • RE penetration
   • RE targets     │  Subject to:             │          trajectory
   • Fuel prices    │   Capacity limits        │        • Transmission
   • Availability   │   RE targets             │          expansion plan
     profiles       │   CO₂ budgets            │        • Estimated NPV cost
                    │   Retirement logic       │
                    └─────────────────────────┘
                              │
                              │ Investment schedule
                              ▼
                    ┌─────────────────────────┐
                    │  OPERATIONAL DISPATCH    │
                    │  (year by year,          │
                    │   rolling horizon)       │
                    └─────────────────────────┘
```


---


## How It Works


### Linear Program

The Master Problem is formulated as a **pure linear program (LP)** — all decision variables are continuous, and retirement is handled by age-based logic evaluated at model construction time. This avoids the computational cost of mixed-integer programming (MIP) at the strategic planning level, enabling solve times of seconds to minutes even for 25-year, multi-node, multi-technology problems.

The LP formulation means investment decisions are continuous (e.g., "invest 87.3 MW of solar PV in year 5"), not discrete ("build or don't build a 100 MW plant"). For capacity planning purposes, continuous investments provide sufficient accuracy; the operational dispatch stage handles the detailed hourly behavior.


### Representative Days

The Master Problem cannot solve full 8760-hour dispatch for every year simultaneously — the resulting model would have hundreds of millions of variables. Instead, it selects a small number of **representative days** (typically 3--10 per year) from the demand profile and validates investment decisions against those days.

Representative days are selected to capture system stress conditions:

- **High-demand days** — days with peak total demand
- **Minimum separation** — at least `min_day_separation` days apart, ensuring coverage across seasons and weather patterns

For each representative day, the Master Problem creates a full set of dispatch variables (generation, storage, curtailment, load shedding) and constraints. This embedded dispatch validates that the proposed investments can actually serve demand under realistic operating conditions — not just on average.

The cost from representative days is scaled to approximate the full year. Accuracy increases with the number of representative days, but so does solve time. Five days is a practical starting point; increase to 7--10 for final studies.

| Representative days | Accuracy | Solve time | Use case |
|---------------------|----------|------------|----------|
| 3 | Low | Fast | Initial screening, parameter tuning |
| 5 | Moderate | Moderate | Standard planning studies |
| 7--10 | High | Slower | Final investment decisions, publications |


### NPV Discounting

All costs are discounted to present value using the configured discount rate:

$$
\text{NPV} = \sum_{y=1}^{Y} \frac{1}{(1+r)^{y-1}} \left[ C^{inv}_y + C^{op}_y + C^{penalty}_y \right]
$$

where:

- \(C^{inv}_y\) — annualized investment costs (generators, batteries, transmission, primary energy, electrolyzers)
- \(C^{op}_y\) — operational costs estimated from representative days (fuel, O&M, startup, battery cycling)
- \(C^{penalty}_y\) — soft constraint violation penalties (unserved demand, RE shortfall, CO₂ excess)

The discount rate has a significant effect on the investment timeline. Higher rates (8--10%) favor deferring capital-intensive investments; lower rates (3--5%) favor early investment in long-lived assets like solar PV or transmission.


---


## Investment Decisions

The Master Problem creates continuous investment variables for each technology, node, and year:

| Investment type | Variable | Units | Description |
|----------------|----------|-------|-------------|
| Generator capacity | \(I^{gen}_{y,g,n}\) | MW | New generation capacity at node \(n\) in year \(y\) |
| Battery power | \(I^{bat,P}_{y,b,n}\) | MW | New charge/discharge capacity |
| Battery energy | \(I^{bat,E}_{y,b,n}\) | MWh | New energy storage capacity |
| Transmission | \(I^{tr}_{y,i,j}\) | MW | Line capacity expansion between nodes \(i\) and \(j\) |
| Fuel storage | \(I^{fs}_{y,f,n}\) | units | Fuel tank capacity (primary energy) |
| Fuel transport | \(I^{ft}_{y,f,i,j}\) | units/day | Fuel pipeline/tanker capacity |

Each investment is bounded by the configured `invest_max_power` (or equivalent) parameter. Setting `invest_max_power: [0.0]` disables investment for that technology at that node.

Investment costs are **annualized** over the equipment lifetime. A solar PV investment of 100 MW at \$800,000/MW with a 25-year lifetime has an annualized cost of \$3,200,000/year (before discounting). This annualization means the optimizer weighs the full lifetime cost, not just the upfront capital.


### Cumulative Capacity

The available capacity in each year is the sum of:

1. **Existing capacity** that has not yet retired (age < lifetime)
2. **All previous investments** that have not yet retired
3. **Degradation** applied as: `effective = rated × (1 - degradation_rate)^age`

This cumulative capacity determines the upper bound on generation and storage in the representative-day dispatch constraints, ensuring that investments are operationally needed.


---


## Retirement Logic

Units retire deterministically when their age exceeds their configured lifetime. No binary retirement decisions are needed.

| Unit type | Age formula | Active condition |
|-----------|-------------|------------------|
| Existing | `initial_age + (year - 1)` | `age < lifetime` |
| Investment | `year - investment_year` | `age < lifetime` |

**Example:** A diesel generator with `initial_age: 10` and `life_time: 30` is active from year 1 through year 20 of the simulation. In year 21, its age reaches 30 and it retires automatically. The optimizer "knows" this will happen and can plan replacement capacity in advance.

Investments also retire. A solar PV investment made in year 5 with `life_time: 25` retires in year 30 (if the simulation extends that far). The optimizer accounts for this when deciding the timing of investments — investing earlier means more years of useful life within the planning horizon.


---


## RE Targets and Policy Constraints

The Master Problem enforces several policy constraints as soft constraints (with penalties):


### RE Penetration Trajectory

The RE target interpolates linearly from `initial_re_penetration` to `target_re_penetration`:

$$
\rho^{target}_y = \rho^{initial} + \frac{y}{Y} \cdot (\rho^{target} - \rho^{initial})
$$

Annual increment bounds limit year-to-year jumps in RE capacity, preventing unrealistically rapid buildout. The RE target is enforced through a penalty term: if the optimizer cannot meet the target economically, it pays the `fre_penalty` cost per MWh of shortfall.


### CO₂ Budget

When configured, a system-wide CO₂ budget constrains total annual emissions. Exceeding the budget incurs a penalty (effectively a carbon price).


### Capacity Adequacy

The system must have enough total installed capacity to meet peak demand plus a reserve margin. A slack variable with high penalty ensures the optimizer invests in enough capacity for reliability.


---


## Configuration

```yaml
master_problem:
  stochastic: false                   # Enable stochastic programming
  representative_days_per_year: 5     # Days per year for operational validation
  min_day_separation: 7               # Minimum days between representatives

  mga:
    enabled: false                    # Enable MGA / SPORES
    method: mga                       # "mga" | "spores"
    num_alternatives: 10              # K alternatives (MGA only)
    objectives: []                    # SPORES menu (SPORES only)
    slack_fraction: 0.05              # Cost slack (5% above optimal)
    investment_threshold: 0.1         # MW threshold for HSJ frequency scoring
```

### Key Parameters

| Parameter | Default | Effect |
|-----------|---------|--------|
| `representative_days_per_year` | 5 | More days = better accuracy, slower solve |
| `min_day_separation` | 7 | Ensures seasonal diversity in selected days |
| `stochastic` | false | Enable scenario-based planning (see [Stochastic Tutorial](../tutorials/stochastic.md)) |
| `mga.enabled` | false | Generate near-optimal alternatives |
| `mga.method` | `"mga"` | `"mga"` runs the HSJ loop $K$ times; `"spores"` solves one alternative per declared objective |
| `mga.slack_fraction` | 0.05 | How far from optimum alternatives can be (5% = accept solutions up to 5% more expensive) |
| `mga.num_alternatives` | 10 | Number of diverse portfolios (MGA only; ignored for SPORES) |
| `mga.objectives` | `[]` | SPORES objective menu — see [SporesObjective](../api/config-schema.md#sporesobjective) (SPORES only; must be empty under MGA) |

Investment candidates are defined through **technologies** (not individual generators):

```yaml
    technologies:
      solar_pv:
        name: Solar PV
        type: Renewable
        fuel: Solar
        invest_cost: [800000.0]       # $/MW
        invest_max_power: [500.0]     # Maximum MW per node
        life_time: [25]
        Availability: solar_pv.csv

      wind:
        name: Wind
        type: Renewable
        fuel: Wind
        invest_cost: [1200000.0]
        invest_max_power: [300.0]
        life_time: [20]
        Availability: wind.csv

    battery_technologies:
      li_ion:
        name: Li-Ion Storage
        invest_cost_power: [200000.0]   # $/MW
        invest_cost_capacity: [150000.0] # $/MWh
        invest_max_power: [200.0]
        invest_max_capacity: [800.0]
        life_time: [15]
```


---


## Interpreting Results

After solving, the Master Problem reports:

### Investment Schedule

```
MASTER PROBLEM RESULTS
Total NPV cost: $145,234,567
Solver time: 12.3 seconds

INVESTMENT DECISIONS:
  Year  1: Solar PV     +87.3 MW at node Main
  Year  1: Li-Ion       +24.1 MW / 96.4 MWh at node Main
  Year  5: Wind         +45.0 MW at node North
  Year  8: Solar PV     +32.7 MW at node Main
  Year 12: Li-Ion       +18.5 MW / 74.0 MWh at node North
  Year 15: Transmission +50.0 MW (Main → North)

RETIREMENT SCHEDULE:
  Year 21: Diesel Gen (initial_age=10, lifetime=30)
  Year 26: Gas Turbine (initial_age=5, lifetime=30)

RE PENETRATION TRAJECTORY:
  Year  1: 62.3%  (target: 52.0%)
  Year  5: 71.8%  (target: 60.0%)
  Year 10: 78.5%  (target: 70.0%)
  Year 25: 85.2%  (target: 80.0%)
```


### What to Look For

| Indicator | Healthy | Problematic |
|-----------|---------|-------------|
| Investment timing | Gradual, spread across years | Everything in year 1 (may indicate too-low costs or too-aggressive targets) |
| RE trajectory | Slightly above targets | Far below targets (check `fre_penalty` — may be too low) |
| Load shedding in rep days | Zero or minimal | Significant shedding (insufficient investment candidates or capacity) |
| Solver time | Seconds to minutes | Hours (reduce representative days or simplify the system) |
| NPV cost | Reasonable for system size | Extremely high (check penalty coefficients) or near-zero (check for free energy bugs) |


---


## Connection to Operational Dispatch

The Master Problem passes its investment schedule to the operational dispatch stage. For each year:

1. Cumulative investments are computed (all investments up to that year, minus retired ones)
2. **Virtual generators and batteries** are created from technology investments and added to the system configuration
3. The operational dispatch solves the full 8760-hour year using rolling horizon windows
4. Actual operational cost, RE penetration, curtailment, and load shedding are computed

The Master Problem's representative-day cost estimates may differ from the actual operational costs. This is expected — representative days are an approximation. For well-configured systems (5+ representative days, reasonable technology mix), the difference is typically small.

If the Master Problem reports low load shedding but operational dispatch shows significant shedding, increase the number of representative days to give the planner a more accurate picture of operational challenges.


---


## MGA and SPORES

When `mga.enabled` is `true`, the solver first finds the cost-optimal solution $C^*$ and adds the constraint $Z \leq C^* \times (1 + \text{slack\_fraction})$. What it does next depends on `mga.method`:

- **`method: mga`** (default) — runs the **Hop-Skip-Jump (HSJ)** loop `num_alternatives` times. Each iteration maximises a single diversity objective weighted by a frequency score that penalises investment variables seen in earlier alternatives.
- **`method: spores`** — runs the [SPORES](../api/config-schema.md#sporesobjective) sweep. One alternative per declared objective (`min_total_build`, `max_tech_equity`, `max_regional_equity`, `evolutionary_dist`). The alternative count equals `len(objectives)`; `num_alternatives` is ignored.

Each alternative portfolio is near-optimal in cost but differs in technology mix and spatial allocation. Alternative 0 is always the cost-optimal solution and is used for operational dispatch. Results are exported under `/mga/` in the system HDF5 file with `attrs["method"]` at the root and a per-alternative `attrs["objective"]` carrying the objective tag.

Typical use cases:

- **Identifying robust decisions** — technologies that appear in every alternative are robust; those that vary are interchangeable
- **Stakeholder engagement** — presenting a range of options rather than a single "optimal" answer
- **Risk assessment** — understanding which investment choices are sensitive to modeling assumptions
- **Targeted policy questions** (SPORES only) — when you need *named* alternatives ("smallest portfolio", "spatially-spread plan", "maximally different from the optimum"), SPORES produces them directly; MGA's HSJ loop discovers diversity without naming it

For the LP formulation see [Capacity Expansion § 15](../formulation/capacity-expansion.md#15-mgaspores-near-optimal-alternative-exploration); for a step-by-step tutorial see [Near-Optimal Alternatives](../tutorials/mga.md).


---


## Stochastic Mode

When `stochastic: true`, the Master Problem creates multiple demand/availability scenarios with probability weights. Investment variables are shared across scenarios (you can only build one system), while operational variables are scenario-specific.

This captures the value of hedging: the optimal investment plan under uncertainty may differ from the plan that is optimal for any single scenario. See [Stochastic Tutorial](../tutorials/stochastic.md) for configuration details.


---


## Performance Tips

| Issue | Solution |
|-------|----------|
| Solve time too long | Reduce `representative_days_per_year` from 10 to 5 |
| Memory exceeded | Reduce planning horizon, coarsen temporal resolution (`temporal.resolution_hours: 2`) |
| Too many investment candidates | Limit `invest_max_power` to zero for technologies not under consideration |
| Solver numerical issues | Reduce penalty coefficients (e.g., `fre_penalty` from 6000 to 600) |
| Inaccurate operational cost estimates | Increase `representative_days_per_year` to 7--10 |
| Investments all in year 1 | Check that discount rate is reasonable (5--8%); verify that technology costs are realistic |
| No investments despite need | Check that `invest_max_power > 0` for candidate technologies; verify that investment costs are not unrealistically high |


---


## Further Reading

- [Mathematical Formulation](../formulation/capacity-expansion.md) — full constraint-by-constraint specification
- [Configuration Reference](../user-guide/configuration.md) — all YAML fields for the master problem
- [Stochastic Tutorial](../tutorials/stochastic.md) — scenario-based planning
- [MGA/SPORES Formulation](../formulation/capacity-expansion.md#15-mgaspores-near-optimal-exploration) — mathematical details of near-optimal exploration
- [Solver Guide](../user-guide/solver-guide.md) — solver selection and performance tuning
