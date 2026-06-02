# Global & System Settings

Two levels: **Global Settings** apply to the entire simulation; **System Settings** are per-system.


---


## Global Settings

Click **Global Settings** in the Element Tree. These parameters apply uniformly across all systems.

### Simulation

| Field | Default | Description |
|-------|---------|-------------|
| Simulation Mode | `development` | `development` = LP-based capacity expansion combined with rolling-horizon economic dispatch. Fast and suitable for long-term planning studies. `unit_commitment` = MIP with binary generator on/off decisions. Captures start-up costs, minimum up/down times, and discrete unit scheduling. More detailed but significantly slower. |
| UC Hours | 24 | Hours per unit commitment window. Only relevant when Simulation Mode is `unit_commitment`. Longer windows capture more operational detail but increase solve time exponentially. |
| Date Start | 01/01/2025 | Simulation start date. The first year of the planning horizon begins on this date. Demand data and availability profiles are aligned to this date. |
| Enable Primary Energy | On | Include the fuel supply chain in the optimization. When enabled, the optimizer jointly optimizes electricity and fuel networks, considering fuel transport costs, storage, and supply constraints. When disabled, fuel is assumed to be available at each generator at its specified cost. |

**Tips:**
- Start with `development` mode for initial design. Switch to `unit_commitment` only after topology is stable (MIP solves take 10-100x longer).
- Date Start determines the base year for cost projections, fuel price growth, and degradation.

### Temporal

| Field | Default | Description |
|-------|---------|-------------|
| Resolution | 1 h | Operational time step for dispatch optimization. Supports 1 hour (standard), 0.5 hours, and 0.25 hours. Finer resolution captures sub-hourly dynamics but increases problem size proportionally. |
| Rolling Horizon Hours | 48 | Size of each dispatch window in hours. The operational year (8760 hours) is divided into overlapping windows of this length. Each window is solved independently as an optimization problem. |
| Overlap Hours | 6 | Overlap between consecutive dispatch windows. The first `overlap_hours` of each window use the final state from the previous window (battery SOC, generator status) as initial conditions. Only results from the non-overlapping portion are kept. |
| Investment Resolution | 8760 h | Time granularity for the master problem (strategic planning). Use 8760 for annual resolution. Lower values (e.g., 4380 for semi-annual) increase planning detail but double the master problem size. |
| Primary Energy Resolution | 168 h | Time step for the fuel supply chain optimization. Default is weekly (168 hours). Only active when Enable Primary Energy is on. |

**Rolling horizon:** The operational year is solved as overlapping windows. Example with 48-hour windows and 6-hour overlap:
- Window 1: hours 1-48 (keep hours 1-42).
- Window 2: hours 43-90 (initial conditions from hour 42, keep hours 43-84).
- Window 3: hours 85-132, and so on.

Overlap ensures smooth SOC continuity and generator on/off transitions between windows.

### Solver

| Field | Default | Description |
|-------|---------|-------------|
| Solver | Auto-detect | Which optimization solver to use. Options: HiGHS (free, default), Gurobi (commercial, requires license), CPLEX (commercial, requires license), CBC (free), GLPK (free). The solver selector auto-detects which solvers are installed on your system and only shows available options. |
| Threads | 4 | Number of parallel solver threads. Set to the number of physical CPU cores for optimal performance. Hyperthreading cores may not provide additional benefit. |
| Time Limit | 300 s | Maximum solve time per optimization problem (per window or per master problem). If the solver cannot find an optimal solution within this limit, it returns the best feasible solution found. Increase for large or difficult problems. |
| Optimality Gap | 0.01 | Relative MIP optimality gap (1% default). The solver stops when it proves the current solution is within this fraction of the true optimum. Only relevant for unit commitment mode (MIP). Set to 0.001 (0.1%) for high-precision studies, or 0.05 (5%) for rapid feasibility checks. |
| Verbose | Off | When enabled, full solver output (iterations, simplex/barrier progress, branching statistics) is printed to the Python console. Useful for diagnosing infeasibility or slow convergence. |

**Recommendations:**
- **HiGHS** -- Best free solver. Excellent for LP problems (development mode). Good MIP performance.
- **Gurobi** -- Premium commercial solver with state-of-the-art MIP performance. Free academic licenses available.
- **CPLEX** -- IBM's commercial solver. Competitive with Gurobi for large MIPs.
- **CBC** -- Free MIP solver. Slower than HiGHS for most problems but well-tested.
- **GLPK** -- Free LP/MIP solver. Suitable only for small problems.

### N-1 Security

Ensures system stability after loss of any single element (one generator or one transmission line).

| Field | Default | Description |
|-------|---------|-------------|
| N-1 Enabled | Off | Master switch for contingency analysis. When enabled, the optimizer adds constraints ensuring survivability under single-element outages. |
| Transmission Enabled | On | Test line outages. For each transmission line, verify that the remaining network can serve all demand without violating thermal limits. |
| Transmission Reserve Factor | 0.70 | Post-contingency line loading limit as a fraction of rated capacity. A value of 0.70 means that after a line outage, no remaining line may be loaded above 70% of its rating. This provides headroom for the redistributed power flows. |
| Critical Threshold | 0.50 | Minimum utilization to consider a line critical. Lines loaded below this threshold in the base case are not tested as contingencies (they are unlikely to cause issues). Reduces computation time. |
| Generation Enabled | On | Test generator outages. Ensure that losing the largest (or most critical) generator does not cause load shedding. |
| Reserve Type | `largest_unit` | How to size the generation reserve requirement: `largest_unit` = reserve >= largest online generator. `percentage` = reserve >= Reserve Percentage x total demand. `fixed` = reserve >= a fixed MW value. |
| Reserve Percentage | 0.15 | Generation capacity reserve margin (15% default). Only used when Reserve Type is `percentage`. |

The optimizer identifies critical elements, simulates their outage, and verifies continued demand service. If infeasible, it adjusts dispatch or invests in additional capacity/transmission.

### Master Problem

Strategic multi-year capacity expansion planning.

| Field | Default | Description |
|-------|---------|-------------|
| Stochastic | Off | Enable multi-scenario stochastic optimization. When enabled, the master problem considers all defined stochastic scenarios simultaneously, weighted by their probabilities. First-stage decisions (investments) are common across scenarios; second-stage decisions (dispatch) vary per scenario. |
| Representative Days | 3 | Number of peak/representative days per year used for operational validation within the master problem. The master problem uses simplified operational snapshots to verify that investment decisions produce feasible dispatch. |
| Min Day Separation | 720 h | Minimum hours between representative days. Ensures that selected days are spread across the year (e.g., summer peak, winter peak, shoulder season) rather than clustered. 720 hours = 30 days minimum separation. |

### MGA / SPORES (Near-Optimal Alternative Exploration)

Generates a set of investment portfolios near the cost-optimal solution. Two methods share the cost-slack envelope but differ in how alternatives are produced (see [Concepts: MGA and SPORES](../getting-started/concepts.md#near-optimal-alternatives-mga-and-spores)):

| Field | Default | Description |
|-------|---------|-------------|
| Enable MGA | Off | Master toggle for the near-optimal sub-section |
| Method | `MGA (HSJ loop)` | `MGA (HSJ loop)` runs the classical loop $K$ times; `SPORES (per-objective)` solves one alternative per declared objective |
| Alternatives | 10 | Number of alternatives $K$ for the HSJ loop. **Hidden when Method = SPORES** (the count equals the number of checked objectives) |
| SPORES Objectives | (none checked) | Multi-select checklist: `HSJ diversity`, `Min total build`, `Tech equity`, `Regional equity`, `Evolutionary distance`. **Hidden when Method = MGA**. Required when Method = SPORES — saving with the method set but no objectives selected will trigger a validation error at run time |
| Cost Slack (fraction) | 0.10 | Maximum cost increase allowed (0.10 = alternatives can cost up to 110% of optimal). Shared by both methods |
| Invest. Threshold (MW) | 0.01 | Minimum investment to count as "selected" by HSJ frequency scoring. Used by `method = mga` and by the SPORES `hsj_diversity` objective; the other SPORES objectives ignore it |

The visible field set updates automatically: choosing `SPORES (per-objective)` hides Alternatives and reveals SPORES Objectives, and vice versa. The objectives box is capped at ~220 px wide so it doesn't dominate the form when active.


---


## System Settings

Click a **system name** in the Element Tree. Each system has independent parameters.

### General

| Field | Default | Description |
|-------|---------|-------------|
| Name | -- | System name (editable). Must be unique. |
| Demand Scale | 1.0 | Multiplier applied to all demand in this system. Use values >1.0 to model demand growth or <1.0 for demand reduction scenarios. |
| Discount Rate | 0.08 | Net Present Value discount rate (8% default). Used to discount future costs to present value for investment comparison. Higher rates favor near-term, lower-cost options. |
| RE Penetration Target | 0.50 | Final-year renewable energy fraction target (50% default). The optimizer penalizes any shortfall from this target using the RE Penetration Loss penalty. |
| Min Annual Increment | 0.02 | Minimum yearly increase in RE penetration (2%). Prevents the optimizer from deferring all RE investment to the final year. |
| Max Annual Increment | 0.10 | Maximum yearly increase in RE penetration (10%). Prevents unrealistically rapid transitions. |
| Loss Demand Threshold | 0.01 | Acceptable unserved energy ratio (1% default). The optimizer aims to keep loss of load below this fraction of total demand. |
| Inertia Threshold | 0.0 | Minimum system inertia requirement in seconds. Set >0 to ensure a minimum level of synchronous generation is online at all times. |
| Simulate Rooftop | Off | Include distributed rooftop PV in the simulation. When enabled, rooftop PV production is subtracted from net demand before dispatch optimization. |

### Cost Limits

| Field | Default | Description |
|-------|---------|-------------|
| Max Annual Cost | $1e12 | Annual investment budget cap. The optimizer cannot spend more than this in any single year. Set to a very large value (default) to have no practical limit. |
| Max NPV Penalty | $1e9 | Penalty coefficient for exceeding the NPV budget. Acts as a soft budget constraint. |
| Max Decommissioning Cost | $1e9 | Cap on total decommissioning expenditure across the planning horizon. |
| Life Extension Factor | 1.5 | Cost multiplier for extending a unit's operational life beyond its rated lifetime. A factor of 1.5 means life extension costs 50% more than normal maintenance. |

### Penalties

Shadow prices controlling constraint violation costs. Higher penalties push the optimizer toward constraint satisfaction. Setting a penalty to zero effectively removes the constraint.

| Penalty | Default | Unit | Description |
|---------|---------|------|-------------|
| Loss of Load | 10,000,000 | $/MWh | Value of lost load (VOLL). The cost assigned to each MWh of unserved energy. This is typically the highest penalty, reflecting the severe economic and social impact of blackouts. |
| Static Reserve | 100 | $/MW | Penalty for spinning reserve shortfall. Applied per MW of reserve deficiency per hour. |
| Dynamic Reserve | 100 | $/MW | Penalty for frequency response reserve shortfall. |
| Inertia | 200 | $/s | Penalty for system inertia below the threshold. Applied per second of inertia deficiency. |
| Curtailment | 100 | $/MWh | Penalty applied to renewable energy curtailment. Higher values discourage curtailment and incentivize storage investment. |
| Max Curtailment Ratio | 0.05 | fraction | Maximum fraction of renewable generation that may be curtailed (5% default). This is a hard constraint, not a penalty: the optimizer enforces `curtailment <= ratio x total_RE_generation`. |
| CO2 Cost | 10 | $/tonne | Carbon price. Applied to all CO2 emissions from fossil fuel combustion. |
| CO2 Budget Violation | 500 | $/tonne | Penalty for exceeding the CO2 budget (if enabled). Acts as a backstop to the carbon price. |
| RE Penetration Loss | 100 | $/MWh | Penalty for shortfall from the RE target. Applied per MWh of "missing" renewable energy relative to the target. |
| EV Loss | 10 | $/MWh | Penalty for unmet EV charging demand. Lower than VOLL because EV charging can often be deferred. |
| Fuel Supply Loss | 100 | $/MWh | Penalty for fuel supply shortfall. Applied when fuel demand exceeds supply. |
| Non-Electric Demand Loss | 100 | $/MWh | Penalty for unmet non-electric demand (heat, hydrogen, etc.). |

#### Criticality Penalties (Load Shedding Priority)

Priority order for load shedding. The optimizer sheds lowest-criticality load first.

| Level | Default | Typical Assignment |
|-------|---------|-------------------|
| Critical | 1,000 $/MWh | Hospitals, emergency services, water treatment, data centers |
| High | 100 $/MWh | Industrial loads, large commercial consumers |
| Medium | 10 $/MWh | Residential loads, small commercial |
| Low | 1 $/MWh | Interruptible loads, non-essential services, street lighting |

Assign demand sectors to criticality levels in the demand configuration. A hospital (Critical) never loses power before a street light (Low).

### CO2 Budget

| Field | Default | Description |
|-------|---------|-------------|
| Enabled | Off | When on, enforce a hard CO2 budget constraint in addition to the carbon price. |
| Annual Budget | 1e6 tonnes/year | Maximum annual CO2 emissions for this system. If emissions exceed this budget, the CO2 Budget Violation penalty applies. |
| Budget Trajectory | Linear | How the budget decreases over the planning horizon: `Linear` (constant annual reduction), `Exponential` (faster initial reduction), or `Constant` (same budget every year). |

### DC Power Flow

Linearized DC power flow model for transmission dispatch.

| Field | Default | Description |
|-------|---------|-------------|
| Enable Angle Limits | On | Enforce voltage angle difference limits across transmission lines. Prevents unrealistic power flows on lightly loaded lines. |
| Max Angle Difference | 30 degrees | Maximum voltage angle difference between connected buses. Typical values range from 15-45 degrees depending on system characteristics. |
| Slack Bus | 0 | Reference bus index for DC power flow angle calculations. The slack bus has angle = 0 by convention. Choose a strong bus (major generation center). |


---


## Stochastic Scenarios

Multiple planning scenarios for robust optimization. Click **Stochastic Scenarios** in the Element Tree.

### Scenario Definition

Each scenario represents an alternative future with different cost assumptions:

| Field | Description |
|-------|-------------|
| Name | Descriptive scenario name (e.g., "High Oil Price", "Low RE Cost", "Climate Extreme") |
| Probability | Weight assigned to this scenario (0.0 to 1.0). All scenario probabilities must sum to 1.0. |

### Cost Multipliers

Multipliers adjusting system-level parameters relative to the base case:

| Multiplier | Description | Example |
|------------|-------------|---------|
| Fuel Cost | Scales all fuel prices | 1.5 = 50% higher fuel costs |
| Demand Growth | Scales demand increase over the planning horizon | 0.8 = 20% less demand growth |
| Investment Cost | Scales capital expenditure for all technologies | 0.7 = 30% cheaper investments (e.g., optimistic technology learning) |
| RE Availability | Scales renewable resource availability profiles | 1.1 = 10% better solar/wind resource (e.g., favorable climate) |

### Using Stochastic Optimization

1. Define at least two scenarios with probabilities summing to 1.0.
2. Enable stochastic mode in **Global Settings > Master Problem > Stochastic**.
3. Run the simulation.

The master problem makes investment decisions performing well across all scenarios (first-stage). Operational dispatch (second-stage) adjusts per-scenario, producing a robust investment plan that hedges against uncertainty.


---


## How Settings Interact

| Level | Scope | Examples |
|-------|-------|---------|
| **Global** | All systems | Solver, temporal resolution, N-1 security, simulation mode |
| **System** | One system | Demand scale, RE targets, penalties, CO2 budgets, discount rate |
| **Stochastic** | Modifies system-level | Fuel cost multiplier, demand growth multiplier |

- Global settings cannot be overridden per system. Different temporal resolutions require separate projects.
- System settings are fully independent. Connected systems can have different RE targets, penalties, and CO2 budgets.
- Stochastic multipliers modify system-level parameters across all systems.


---


## Settings Persistence

- Saved as part of the project file (`Ctrl+S`).
- Exported YAML files (`Ctrl+Shift+S`) include all settings.
- Defaults used for unconfigured settings (matching the editor's initial values).
- User-modified settings highlighted with a subtle background color.


---


## Quick Reference: Recommended Settings by Study Type

| Study Type | Simulation Mode | Resolution | Rolling Horizon | Solver | N-1 |
|------------|----------------|------------|-----------------|--------|-----|
| Screening study | `development` | 1 h | 48 h | HiGHS | Off |
| Detailed planning | `development` | 1 h | 48 h | Gurobi | On |
| Operational study | `unit_commitment` | 0.5 h | 24 h | Gurobi | On |
| Rapid feasibility | `development` | 1 h | 168 h | HiGHS | Off |
| Stochastic planning | `development` | 1 h | 48 h | Gurobi | Off |


---


## Troubleshooting Common Settings Issues

| Problem | Likely Cause | Solution |
|---------|-------------|----------|
| Solver reports infeasible | Penalties too low or conflicting constraints | Increase VOLL penalty, relax RE target |
| Very slow solve | UC mode with many generators, small windows | Use `development` mode, increase window size |
| Unrealistic curtailment | `max_curtailment_ratio` too high | Reduce to 0.05 (5%) |
| No battery investment | Curtailment penalty too low | Increase curtailment penalty, reduce `max_curtailment_ratio` |
| Memory issues | Too many years × nodes × generators | Reduce planning horizon, aggregate nodes |
| Numerical warnings | Poor problem scaling | Enable solver verbose mode, check penalty magnitudes |


---


## Keyboard Shortcuts for Settings

| Shortcut | Action |
|----------|--------|
| `Ctrl+S` | Save all settings to project file |
| `Ctrl+Shift+S` | Export settings to YAML |
| `Ctrl+Z` | Undo last change in form |
| `F5` | Reset form to saved values |
