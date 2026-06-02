# Capacity Expansion (Master Problem)


The **Master Problem** (`master_problem.jl`) determines optimal investment and retirement decisions over a multi-year planning horizon by minimizing the net present value (NPV) of total system costs. Representative days approximate operational costs and validate that investment plans are operationally feasible. Notation follows the conventions in the [Formulation Overview](overview.md).

---


## 1. Overview


The Master Problem is a **two-stage linear program** [**[24]**](../reference/bibliography.md#ref24), [**[25]**](../reference/bibliography.md#ref25):

1. **First stage (investment)**: Capacity additions for generators, batteries, transmission, and primary energy infrastructure across all planning years.
2. **Second stage (operational validation)**: Representative-day dispatch subproblems embedded within the master model to link investment decisions to operational feasibility.

Retirement is handled through **age-based expiry** -- units automatically leave service when their age exceeds their configured lifetime. No binary retirement decisions are needed, keeping the formulation as a pure LP.

**Entry point**: `create_master_problem(input; use_representative_days=true)`

---


## 2. Sets


| Symbol | Description | Source |
|--------|-------------|--------|
| \(\mathcal{Y} = \{1, \ldots, Y\}\) | Planning years | `input.years` |
| \(\mathcal{G} = \{1, \ldots, G\}\) | Generator types | `input.generators` |
| \(\mathcal{G}_{RE} \subseteq \mathcal{G}\) | Renewable generators | `gen.type == "Renewable"` |
| \(\mathcal{B} = \{1, \ldots, B\}\) | Battery types | `input.batteries` |
| \(\mathcal{N} = \{1, \ldots, N\}\) | Nodes (buses) | `input.network.num_buses` |
| \(\mathcal{T}_d = \{1, \ldots, H\}\) | Hours within a representative day | `24 / temporal_resolution_hours` |
| \(\mathcal{D}_y\) | Representative days for year \(y\) | `select_representative_days()` |
| \(\mathcal{F}\) | Fuel types (primary energy) | `input.pe_configs` |
| \(\mathcal{S}\) | Demand sectors | `input.sectoral_demand` |

---


## 3. Decision Variables

### 3.1 Investment Variables


| Variable | Domain | Units | Description | Julia name |
|----------|--------|-------|-------------|------------|
| \(I^{gen}_{y,g,n}\) | \(\mathbb{R}_+\) | MW | Generator capacity investment | `gen_investment[y][g][n]` |
| \(I^{bat,P}_{y,b,n}\) | \(\mathbb{R}_+\) | MW | Battery power investment | `bat_power_investment[y][b][n]` |
| \(I^{bat,E}_{y,b,n}\) | \(\mathbb{R}_+\) | MWh | Battery energy investment | `bat_capacity_investment[y][b][n]` |
| \(I^{tr}_{y,i,j}\) | \(\mathbb{R}_+\) | MW | Transmission expansion | `transfer_investment[y][(i,j)]` |
| \(I^{fs}_{y,f,n}\) | \(\mathbb{R}_+\) | units | Fuel storage investment | `fuel_storage_investment[f][y][n]` |
| \(I^{ft}_{y,f,i,j}\) | \(\mathbb{R}_+\) | units/day | Fuel transport investment | `fuel_transport_investment[f][y][(i,j)]` |

### 3.2 RE Tracking and Slack Variables


| Variable | Domain | Units | Description | Julia name |
|----------|--------|-------|-------------|------------|
| \(\rho_y\) | \([0, 1]\) | -- | RE penetration ratio | `re_penetration_ratio[y]` |
| \(s^{re}_y\) | \(\mathbb{R}_+\) | -- | RE target slack | `slack_re_target[y]` |
| \(s^{cap}_{y,n}\) | \(\mathbb{R}_+\) | MW | Capacity adequacy slack | `slack_capacity[(y,n)]` |
| \(s^{bud}_y\) | \(\mathbb{R}_+\) | \$ | Budget slack | `slack_budget[y]` |

### 3.3 Representative Day Operational Variables


For each year \(y\) and representative day \(d\), a set of dispatch variables is created (struct `RepresentativeDayVariables`):

| Variable | Domain | Units | Description | Julia name |
|----------|--------|-------|-------------|------------|
| \(P^{day}_{g,n,t}\) | \(\mathbb{R}_+\) | MW | Generator output | `gen_output[g,n,t]` |
| \(C^{day}_{g,n,t}\) | \(\mathbb{R}_+\) | MW | Curtailment per generator | `curtailment[g,n,t]` |
| \(P^{ch,day}_{b,n,t}\) | \(\mathbb{R}_+\) | MW | Battery charge | `bat_charge[b,n,t]` |
| \(P^{dis,day}_{b,n,t}\) | \(\mathbb{R}_+\) | MW | Battery discharge | `bat_discharge[b,n,t]` |
| \(E^{day}_{b,n,t}\) | \(\mathbb{R}_+\) | MWh | Battery SOC | `bat_soc[b,n,t]` |
| \(L^{day}_{n,t}\) | \(\mathbb{R}_+\) | MW | Loss of load | `loss_load[n,t]` |
| \(\phi^{day}_{i,j,t}\) | \(\mathbb{R}\) | MW | Transfer flow | `transfer[(i,j)][t]` |
| \(\lambda^{fre}_{n,t}\) | \(\mathbb{R}_+\) | MW | FRE penetration loss slack | `fre_penetration_loss[n,t]` |
| \(L^{sec,day}_{s,n,t}\) | \(\mathbb{R}_+\) | MW | Sectoral loss of load | `loss_of_load_sectoral[s][n,t]` |

---


## 4. Parameters

### 4.1 Generator Parameters


| Symbol | Units | Description | Julia field |
|--------|-------|-------------|-------------|
| \(\bar{P}_{g,n}\) | MW | Rated power | `gen.rated_power[n]` |
| \(c^{inv}_{g,n}\) | \$/MW | Investment cost | `gen.invest_cost[n]` |
| \(\bar{I}_{g,n}\) | MW | Maximum investment | `gen.invest_max[n]` |
| \(\tau_{g,n}\) | years | Lifetime | `gen.life_time[n]` |
| \(a^0_{g,n}\) | years | Initial age | `gen.initial_age[n]` |
| \(\delta_{g,n}\) | [0,1] | Annual degradation rate | `gen.degradation_rate[n]` |
| \(\alpha_{g,t,n}\) | [0,1] | Availability factor | `gen.availability[t,n]` |
| \(c^{fuel}_{g,n}\) | \$/MWh | Fuel cost | `gen.fuel_cost[n]` |
| \(c^{fix}_{g,n}\) | \$/MWh | Fixed O&M cost | `gen.fixed_cost[n]` |
| \(c^{maint}_{g,n}\) | \$/MWh | Maintenance cost | `gen.maintenance_cost[n]` |

### 4.2 Battery Parameters


| Symbol | Units | Description | Julia field |
|--------|-------|-------------|-------------|
| \(\bar{P}^{bat}_{b,n}\) | MW | Max discharge power | `bat.max_discharge_power[n]` |
| \(\bar{E}_{b,n}\) | MWh | Energy capacity | `bat.capacity[n]` |
| \(\eta^{ch}_{b,n}\) | [0,1] | Charge efficiency | `bat.charge_efficiency[n]` |
| \(\eta^{dis}_{b,n}\) | [0,1] | Discharge efficiency | `bat.discharge_efficiency[n]` |
| \(E^0_{b,n}\) | [0,1] | Initial SOC fraction | `bat.soc_initial[n]` |
| \(h^{min}_{b}\) | hours | Minimum duration | `bat.min_duration_hours` |
| \(h^{max}_{b}\) | hours | Maximum duration | `bat.max_duration_hours` |
| \(c^{\text{thr}}_{b,n}\) | \$/MWh | Throughput degradation cost | `bat.throughput_degradation_cost[n]` |

### 4.3 System Parameters


| Symbol | Units | Description | Julia field |
|--------|-------|-------------|-------------|
| \(r\) | [0,1] | Discount rate | `input.discount_rate` |
| \(\bar{B}_y\) | \$ | Max annual investment budget | `input.max_annual_investment` |
| \(\rho^{target}\) | [0,1] | Target RE penetration (final year) | `input.target_re_penetration` |
| \(\rho^{init}\) | [0,1] | Initial RE penetration | `input.initial_re_penetration` |
| \(\Delta\rho^{min}\) | [0,1] | Minimum annual RE increment | `input.min_re_increment` |
| \(\Delta\rho^{max}\) | [0,1] | Maximum annual RE increment | `input.max_re_increment` |
| \(M_{res}\) | -- | Reserve margin multiplier | `input.reserve_margin` (default 1.15) |
| \(\gamma\) | -- | Annual demand growth rate | `input.demand_growth` |
| \(c^{VOLL}\) | \$/MW | Value of lost load penalty | `input.loss_of_load_penalty` |
| \(c^{fre}\) | \$/MWh | FRE penetration loss penalty | `input.fre_penetration_loss_penalty` |
| \(c^{slack}\) | \$ | Generic slack penalty | `input.slack_penalty` |
| \(\bar{\kappa}\) | [0,1] | Maximum curtailment ratio | `input.max_curtailment_ratio` (default 0.05) |
| \(D_r\) | -- | Representative days per year | `input.representative_days_per_year` |

---


## 5. Objective Function


**Julia function**: `build_master_objective!(model, vars, input)`

The objective minimizes the NPV of total system costs over the planning horizon:

\[
\min \; Z = \sum_{y=1}^{Y} \frac{1}{(1+r)^{y-1}} \left[ C^{inv}_y + C^{op}_y \right] + C^{slack} \tag{OBJ-1}
\]

### 5.1 Investment Cost


\[
C^{inv}_y = \underbrace{\sum_{g \in \mathcal{G}} \sum_{n \in \mathcal{N}} c^{inv}_{g,n} \cdot I^{gen}_{y,g,n}}_{\text{Generator}} + \underbrace{\sum_{b \in \mathcal{B}} \sum_{n \in \mathcal{N}} \left( c^{inv,P}_{b,n} \cdot I^{bat,P}_{y,b,n} + c^{inv,E}_{b,n} \cdot I^{bat,E}_{y,b,n} \right)}_{\text{Battery}} \tag{OBJ-2}
\]

\[
\quad + \underbrace{\sum_{(i,j)} c^{tr}_i \cdot I^{tr}_{y,i,j}}_{\text{Transmission}} + \underbrace{\sum_{f \in \mathcal{F}} \left( \sum_{n} c^{fs}_{f,n} \cdot I^{fs}_{y,f,n} + \sum_{(i,j)} c^{ft}_f \cdot d_{ij} \cdot I^{ft}_{y,f,i,j} \right)}_{\text{Primary energy}} \tag{OBJ-3}
\]

where \(d_{ij}\) is the distance between nodes \(i\) and \(j\), and \(c^{ft}_f\) is the transport investment cost per unit per km for fuel \(f\).

### 5.2 Operational Cost


Operational costs come from the representative day subproblems, scaled to annual:

\[
C^{op}_y = \frac{365}{D_r} \sum_{d \in \mathcal{D}_y} C^{day}_{y,d} \tag{OBJ-4}
\]

where the cost for each representative day is (from `calculate_day_operational_cost`):

\[
C^{day}_{y,d} = \sum_{t=1}^{H} \left[ \sum_{g,n} \left(c^{fuel}_{g,n} + c^{fix}_{g,n} + c^{maint}_{g,n}\right) P^{day}_{g,n,t} + \sum_{n} c^{VOLL} \cdot L^{day}_{n,t} + \sum_{n} c^{fre} \cdot \Delta t \cdot \lambda^{fre}_{n,t} \right] \tag{OBJ-5}
\]

\[
\quad + \sum_{t=1}^{H} \sum_{s \in \mathcal{S}} \sum_{n} c^{VOLL} \cdot \kappa_s \cdot L^{sec,day}_{s,n,t} \tag{OBJ-6}
\]

where \(\kappa_s\) is the criticality weight for sector \(s\) and \(\Delta t\) is `temporal_resolution_hours`.

!!! note "PWL cost curves in the master problem"
    When generators or technologies have a `fuel_cost_curve` configured, the representative day operational cost (OBJ-5) uses the same piecewise-linear (PWL) fuel cost decomposition as the operational dispatch formulation. Generator output is decomposed into segments with non-decreasing marginal costs (see [Operational Dispatch -- PWL Fuel Cost](operational-dispatch.md#311-piecewise-linear-pwl-fuel-cost-decomposition)). The same applies to batteries with `discharge_cost_curve`. This ensures that investment decisions in the master problem account for the actual shape of generator cost curves rather than assuming flat marginal costs.

### 5.3 Slack Penalties


\[
C^{slack} = \sum_{y=1}^{Y} \left[ c^{slack} \cdot s^{re}_y + c^{slack} \cdot s^{bud}_y + \sum_{n \in \mathcal{N}} c^{slack} \cdot s^{cap}_{y,n} \right] \tag{OBJ-7}
\]

---


## 6. Cumulative Capacity Expressions


**Julia function**: `build_cumulative_capacity_expressions(vars, input, year_idx)`

A key building block for all constraints. For each technology and node, the cumulative available capacity in year \(y\) combines existing (degraded) capacity with invested capacity, both subject to age-based retirement.

### 6.1 Generator Cumulative Capacity


\[
\bar{P}^{cum}_{y,g,n} = \underbrace{\bar{P}_{g,n} \cdot (1 - \delta_{g,n})^{a^{exist}_{y,g,n}} \cdot \mathbb{1}\!\left[a^{exist}_{y,g,n} < \tau_{g,n}\right]}_{\text{Existing capacity (degraded)}} + \underbrace{\sum_{\substack{y'=1 \\ a^{inv}_{y,y'} < \tau_{g,n}}}^{y} (1 - \delta_{g,n})^{a^{inv}_{y,y'}} \cdot I^{gen}_{y',g,n}}_{\text{Invested capacity (degraded)}} \tag{CUM-1}
\]

### 6.2 Age Calculations


| Unit Type | Age Formula | Indicator Variable | Active Condition |
|-----------|------------|--------------------|------------------|
| Existing  | \(a^{exist}_{y,g,n} = a^0_{g,n} + (y - 1)\) | -- | \(a^{exist}_{y,g,n} < \tau_{g,n}\) |
| Investment (year \(y'\)) | \(a^{inv}_{y,y'} = y - y'\) | -- | \(a^{inv}_{y,y'} < \tau_{g,n}\) |

The indicator function \(\mathbb{1}[\cdot]\) is evaluated at model construction time (not as a binary variable), which keeps the formulation as a pure LP.

### 6.3 Battery Cumulative Capacity


Battery cumulative power and energy follow the same structure but without degradation:

\[
\bar{P}^{bat,cum}_{y,b,n} = \bar{P}^{bat}_{b,n} \cdot \mathbb{1}\!\left[a^{exist}_{y,b,n} < \tau_{b,n}\right] + \sum_{\substack{y'=1 \\ a^{inv}_{y,y'} < \tau_{b,n}}}^{y} I^{bat,P}_{y',b,n} \tag{CUM-2}
\]

\[
\bar{E}^{cum}_{y,b,n} = \bar{E}_{b,n} \cdot \mathbb{1}\!\left[a^{exist}_{y,b,n} < \tau_{b,n}\right] + \sum_{\substack{y'=1 \\ a^{inv}_{y,y'} < \tau_{b,n}}}^{y} I^{bat,E}_{y',b,n} \tag{CUM-3}
\]

### 6.4 Transmission Cumulative Capacity


\[
\bar{F}^{cum}_{y,i,j} = \bar{F}_{i,j} + \sum_{y'=1}^{y} I^{tr}_{y',i,j} \tag{CUM-4}
\]

---


## 7. Constraint Families

### 7.1 Investment Limits (INV)


**Julia function**: `add_investment_constraints!(model, vars, input)`

**INV-1: Cumulative investment limit per generator.**
Total investment across all years must not exceed the maximum allowed:

\[
\sum_{y=1}^{Y} I^{gen}_{y,g,n} \leq \bar{I}_{g,n} \qquad \forall\, g \in \mathcal{G},\; n \in \mathcal{N} \tag{INV-1}
\]

**INV-2: Cumulative battery power investment limit.**

\[
\sum_{y=1}^{Y} I^{bat,P}_{y,b,n} \leq \bar{I}^{bat,P}_{b,n} \qquad \forall\, b \in \mathcal{B},\; n \in \mathcal{N} \tag{INV-2a}
\]

**INV-3: Cumulative battery energy investment limit.**

\[
\sum_{y=1}^{Y} I^{bat,E}_{y,b,n} \leq \bar{I}^{bat,E}_{b,n} \qquad \forall\, b \in \mathcal{B},\; n \in \mathcal{N} \tag{INV-2b}
\]

**INV-4: Battery duration constraints.** Applied per year to each year's investment independently (not cumulative):

\[
I^{bat,E}_{y,b,n} \geq h^{min}_{b} \cdot I^{bat,P}_{y,b,n} \qquad \forall\, y,\, b,\, n \tag{INV-3a}
\]

\[
I^{bat,E}_{y,b,n} \leq h^{max}_{b} \cdot I^{bat,P}_{y,b,n} \qquad \forall\, y,\, b,\, n \tag{INV-3b}
\]

### 7.2 Budget Constraint (BUD)


**Julia function**: `add_budget_constraints!(model, vars, input)`

The annual investment expenditure must not exceed the budget (with slack):

\[
\sum_{g,n} c^{inv}_{g,n} I^{gen}_{y,g,n} + \sum_{b,n} \left( c^{inv,P}_{b,n} I^{bat,P}_{y,b,n} + c^{inv,E}_{b,n} I^{bat,E}_{y,b,n} \right) + \sum_{(i,j)} c^{tr}_i I^{tr}_{y,i,j} \leq \bar{B}_y + s^{bud}_y \tag{BUD-1}
\]

### 7.3 Transmission Symmetry (TXN)


**Julia function**: `add_transmission_symmetry_constraints!(model, vars, input)`

Bidirectional transmission investment is symmetric:

\[
I^{tr}_{y,i,j} = I^{tr}_{y,j,i} \qquad \forall\, y,\; i < j \tag{TXN-1}
\]

### 7.4 Capacity Adequacy (CAP)


**Julia function**: `add_capacity_adequacy_constraints!(model, vars, input)`

Total generation and storage capacity must meet peak demand with a reserve margin at each node:

\[
\bar{P}^{cum}_{y,g,n} + \bar{P}^{bat,cum}_{y,b,n} + s^{cap}_{y,n} \geq D^{peak}_{y,n} \cdot M_{res} \qquad \forall\, y,\; n \tag{CAP-1}
\]

where the peak demand at node \(n\) in year \(y\) is:

\[
D^{peak}_{y,n} = \max_{t} \left[ D_{t,n} \right] \cdot \phi_n \cdot (1 + \gamma)^{y-1} \tag{CAP-2}
\]

The full expression for total capacity in CAP-1 sums across all generators and batteries using the cumulative expressions from equations (CUM-1) and (CUM-2).

### 7.5 RE Target Constraints (RE)


**Julia functions**: `calculate_target_ratios(input)`, `add_re_target_constraints!()`, `add_re_increment_constraints!()`

**RE-1: Target ratio calculation.** Linear interpolation from initial to target penetration:

\[
\rho^{target}_y = \rho^{init} + \frac{y - 1}{Y - 1} \left( \rho^{target} - \rho^{init} \right) \tag{RE-1}
\]

with annual increment clamping:

\[
\rho^{target}_y = \begin{cases}
\rho^{target}_{y-1} + \Delta\rho^{min} & \text{if } \rho^{target}_y - \rho^{target}_{y-1} < \Delta\rho^{min} \\
\rho^{target}_{y-1} + \Delta\rho^{max} & \text{if } \rho^{target}_y - \rho^{target}_{y-1} > \Delta\rho^{max} \\
\rho^{target}_y & \text{otherwise}
\end{cases}
\]

**RE-2: RE penetration equality.** The ratio variable is forced to match the target exactly:

\[
\rho_y = \rho^{target}_y \qquad \forall\, y \in \mathcal{Y} \tag{RE-2}
\]

**RE-3: RE increment bounds.** Annual change in the ratio variable is bounded:

\[
\Delta\rho^{min} \leq \rho_y - \rho_{y-1} \leq \Delta\rho^{max} \qquad \forall\, y \geq 2 \tag{RE-3}
\]

### 7.6 Age-Based Retirement (RET)


**Julia function**: `add_retirement_cascade_constraints!()` (no-op; retirement handled implicitly)

Retirement is enforced structurally through the cumulative capacity expressions (Section 6). A unit contributes capacity only if its age is below its lifetime:

**RET-1: Existing unit retirement.**

\[
\text{Active}(y, g, n) \iff a^0_{g,n} + (y - 1) < \tau_{g,n} \tag{RET-1}
\]

**RET-2: Investment retirement.** An investment made in year \(y'\) retires when:

\[
\text{Active}(y, y', g, n) \iff y - y' < \tau_{g,n} \tag{RET-2}
\]

These conditions are evaluated at model construction time. When the condition is false, the corresponding term is simply omitted from the capacity expression -- no binary variables are needed.

---


## 8. Representative Day Validation


Representative day subproblems link strategic investment decisions to operational feasibility. Without them, the Master Problem could select investments that are infeasible in practice.

### 8.1 Representative Day Selection


**Julia function**: `select_representative_days(demand, year_idx, num_days, min_separation, timesteps_per_day, timesteps_per_year)`

The algorithm selects high-demand days with temporal diversity:

1. Compute daily peak demand for each day in the year: \(D^{peak}_d = \max_t \sum_n D_{d,t,n}\)
2. Select the global peak day first.
3. Divide the remaining year into \(D_r\) segments for seasonal diversity.
4. Within each segment, select the day with the highest peak that satisfies a minimum separation constraint (\(\geq\) `min_day_separation` days from any already-selected day).

### 8.2 Operational Variables


**Julia function**: `create_day_operational_vars!(model, input, year_idx, day_idx, hours)`

For each year \(y\) and representative day \(d\), a full set of hourly dispatch variables is created as described in Section 3.3. These variables are embedded within the same JuMP model as the investment variables, allowing the solver to jointly optimize investment and dispatch.

### 8.3 Operational Constraints


**Julia function**: `add_day_operational_constraints!(model, day_vars, vars, input, year_idx, day_idx, demand, start_hour)`

#### 8.3.1 Power Balance

\[
\sum_{g} P^{day}_{g,n,t} + \sum_{b} P^{dis,day}_{b,n,t} + \sum_{j} \phi^{day}_{j \to n,t} + L^{day}_{n,t} + \sum_{s} L^{sec,day}_{s,n,t} = D^{day}_{n,t} + \sum_{b} P^{ch,day}_{b,n,t} + \sum_{j} \phi^{day}_{n \to j,t} \tag{PB-1}
\]

where the demand at bus \(n\), hour \(t\) in year \(y\) is:

\[
D^{day}_{n,t} = D_{t, \text{parent}(n)} \cdot \phi_n \cdot (1 + \gamma)^{y-1} \tag{PB-2}
\]

Note that curtailment does **not** appear in the power balance. It is handled separately per generator for renewables.

#### 8.3.2 Generator Capacity Linked to Investment

For **renewable** generators:

\[
P^{day}_{g,n,t} \leq \bar{P}^{cum}_{y,g,n} \cdot \alpha_{g,t',n} \qquad \forall\, g \in \mathcal{G}_{RE} \tag{GEN-1}
\]

where \(t' = \text{mod1}(\text{start\_hour} + t - 1,\; 8760)\) maps the representative day hour to the annual availability profile.

For **conventional** generators:

\[
P^{day}_{g,n,t} \leq \bar{P}^{cum}_{y,g,n} \qquad \forall\, g \in \mathcal{G} \setminus \mathcal{G}_{RE} \tag{GEN-2}
\]

The cumulative capacity \(\bar{P}^{cum}_{y,g,n}\) is the expression from Eq. (CUM-1), which is a **linear expression** in the investment variables. This is the key coupling between investment decisions and dispatch feasibility.

#### 8.3.3 Battery Constraints Linked to Investment

**Power limits:**

\[
P^{ch,day}_{b,n,t} \leq \bar{P}^{bat,cum}_{y,b,n} \tag{BAT-1a}
\]

\[
P^{dis,day}_{b,n,t} \leq \bar{P}^{bat,cum}_{y,b,n} \tag{BAT-1b}
\]

**SOC capacity limit:**

\[
E^{day}_{b,n,t} \leq \bar{E}^{cum}_{y,b,n} \tag{BAT-2}
\]

**SOC dynamics:**

\[
E^{day}_{b,n,1} = E^0_{b,n} \cdot \bar{E}^{cum}_{y,b,n} + \eta^{ch}_{b,n} \cdot P^{ch,day}_{b,n,1} - \frac{P^{dis,day}_{b,n,1}}{\eta^{dis}_{b,n}} \tag{BAT-3a}
\]

\[
E^{day}_{b,n,t} = E^{day}_{b,n,t-1} + \eta^{ch}_{b,n} \cdot P^{ch,day}_{b,n,t} - \frac{P^{dis,day}_{b,n,t}}{\eta^{dis}_{b,n}} \qquad \forall\, t \geq 2 \tag{BAT-3b}
\]

**Cyclic SOC constraint** (prevents batteries from acting as infinite energy sources):

\[
E^{day}_{b,n,H} = E^0_{b,n} \cdot \bar{E}^{cum}_{y,b,n} \tag{BAT-4}
\]

#### 8.3.4 Transmission Constraints Linked to Investment

\[
-\bar{F}^{cum}_{y,i,j} \leq \phi^{day}_{i,j,t} \leq \bar{F}^{cum}_{y,i,j} \tag{TRX-1}
\]

!!! note "PWL Transmission Losses in the Master Problem"
    When DC power flow is enabled, the master problem uses the same piecewise linear (PWL) loss model as operational dispatch, but with **fewer segments** (default 2 vs. 3) for computational performance. Losses are split 50/50 between the two bus endpoints of each line (half-loss split), ensuring symmetric loss allocation regardless of flow direction. The number of segments is configurable via `dc_power_flow.pwl_loss_segments_master`. See [DC Power Flow -- Transmission Losses](dc-power-flow.md#transmission-losses) for the full PWL formulation.

#### 8.3.5 RE Penetration per Day

Total renewable generation must meet the target ratio for each representative day:

\[
\sum_{g \in \mathcal{G}_{RE}} \sum_{n,t} P^{day}_{g,n,t} + \sum_{n,t} \lambda^{fre}_{n,t} \geq \rho^{target}_y \cdot D^{total}_{y,d} \tag{RE-DAY-1}
\]

where \(D^{total}_{y,d} = \sum_{n,t} D^{day}_{n,t}\) is the total demand for this day (with growth). The slack variable \(\lambda^{fre}\) allows soft enforcement, penalized in the objective.

Additionally, the ratio variable is linked to actual generation:

\[
\rho_y \cdot D^{total}_{y,d} \leq \sum_{g \in \mathcal{G}_{RE}} \sum_{n,t} P^{day}_{g,n,t} + \sum_{n,t} \lambda^{fre}_{n,t} \tag{RE-DAY-2}
\]

#### 8.3.6 Curtailment Limit per Day

When `max_curtailment_ratio < 1.0`, curtailment is limited to a fraction of renewable generation:

\[
\sum_{g,n,t} C^{day}_{g,n,t} \leq \bar{\kappa} \cdot \sum_{g \in \mathcal{G}_{RE}} \sum_{n,t} P^{day}_{g,n,t} \tag{CURT-1}
\]

This forces investment in storage rather than relying on curtailment to manage surplus RE.

#### 8.3.7 Sectoral Loss-of-Load Bounds

Each sector's load shedding cannot exceed the sector's demand at that node and hour:

\[
L^{sec,day}_{s,n,t} \leq D^{sec}_{s,n,t} \cdot \phi_n \cdot (1+\gamma)^{y-1} \qquad \forall\, s,\, n,\, t \tag{SEC-1}
\]

### 8.4 Cost Scaling


Each representative day's operational cost is scaled to approximate annual costs:

\[
C^{op}_y = \frac{365}{|\mathcal{D}_y|} \sum_{d \in \mathcal{D}_y} C^{day}_{y,d} \tag{SCALE-1}
\]

**Julia function**: `add_representative_days_validation!(model, vars, input, targets)` orchestrates the creation of variables, constraints, and cost expressions for all representative days across all years.

### 8.5 Time-Series Aggregation Method (TSAM)


When `use_tsam = true`, the peak-demand-based day selection (Section 8.1) is replaced by data-driven clustering with inter-period SOC linking.

**Python function**: `compute_tsam_periods()` in `models/tsam.py`
**Julia function**: `add_tsam_periods_validation!(model, vars, input, targets)`

#### 8.5.1 Clustering Algorithm

The annual demand series is reshaped into daily blocks and clustered using k-medoids (default) or k-means:

1. Build feature matrix \(F \in \mathbb{R}^{365 \times (H \cdot N)}\) from daily demand blocks
2. Optionally concatenate normalized availability profiles as additional features
3. Standardize features (zero mean, unit variance)
4. Apply k-medoids: select \(K\) cluster medoids as representative periods
5. Assign each original day to the nearest cluster

#### 8.5.2 Period Weights

Each representative period \(p\) has a weight equal to its cluster size:

\[
w_p = |\mathcal{C}_p|, \qquad \sum_{p=1}^{K} w_p = 365 \tag{TSAM-W}
\]

The operational cost scaling becomes:

\[
C^{op}_y = \sum_{p=1}^{K} w_p \cdot C^{day}_{y,p} \tag{TSAM-COST}
\]

This replaces the uniform \(365/N\) scaling (SCALE-1).

#### 8.5.3 Inter-Period SOC Linking

To enable seasonal storage representation, inter-period SOC boundary variables are introduced:

**Variables**: \(\text{SOC}^{bnd}_{y,b,n,p}\) for \(p = 0, 1, \ldots, K\), where \(p=0\) represents the year start.

**SOC chain** (periods ordered chronologically):

\[
\text{SOC}_{b,n,1}^{(p)} = \text{SOC}^{bnd}_{y,b,n,p-1} + \eta^{ch} \cdot p^{ch}_{b,n,1} - \frac{p^{dis}_{b,n,1}}{\eta^{dis}} \tag{TSAM-SOC1}
\]

\[
\text{SOC}_{b,n,T}^{(p)} = \text{SOC}^{bnd}_{y,b,n,p} \tag{TSAM-SOC2}
\]

**Year-cyclic constraint**:

\[
\text{SOC}^{bnd}_{y,b,n,K} = \text{SOC}^{bnd}_{y,b,n,0} \tag{TSAM-CYC}
\]

**Capacity bound**:

\[
0 \leq \text{SOC}^{bnd}_{y,b,n,p} \leq \bar{E}^{total}_{b,n,y} \quad \forall p \tag{TSAM-CAP}
\]

When `tsam_inter_period_linking = false`, the standard cyclic SOC constraint (SOC-CYC) is used per period instead, and only the weighted cost scaling is active.

---


## 9. NPV-Based Iterative Retirement


**Julia function**: `solve_with_npv_iteration(input; max_iterations=5, npv_threshold=0.0)`

After the initial solve, this procedure identifies units that are economically unviable and forces their retirement through iterative re-optimization.

### 9.1 Algorithm


```
1. Solve initial Master Problem
2. For iter = 1 to max_iterations:
   a. For each unit (generator g at node n, battery b at node n):
      - Calculate NPV over remaining lifetime
   b. Identify units with NPV < threshold
   c. If none found: CONVERGED, return
   d. Force retirement of negative-NPV units
   e. Re-solve Master Problem
3. Return result (converged or max iterations reached)
```

### 9.2 Unit NPV Calculation


**Julia function**: `calculate_unit_npv(result, input, unit_type, unit_idx, node)`

For a generator with remaining life \(L_{rem} = \max(0, \tau_{g,n} - a^0_{g,n})\):

\[
\text{NPV}_g = \sum_{y=1}^{\min(L_{rem}, Y)} \frac{R_y - C_y}{(1+r)^y} \tag{NPV-1}
\]

where:

- **Revenue estimate**: \(R_y = \bar{P}_{g,n} \cdot c^{inv}_{g,n} \cdot r_{return}\) (capacity value at configurable return rate, default 10% for generators)
- **Cost**: \(C_y = \bar{P}_{g,n} \cdot (c^{fix}_{g,n} + c^{maint}_{g,n}) \cdot 8760\)

Units with \(\text{NPV}_g < 0\) are flagged for retirement.

### 9.3 Result


The function returns an `NPVIterationResult` containing:

- Number of iterations performed
- Whether convergence was achieved
- Final `MasterProblemResult`
- List of all forced retirements (`UnitNPV` records)
- NPV history across iterations

---


## 10. Multi-System Master Problem


**Julia function**: `create_multi_system_master_problem(input::MultiSystemMasterInput)`

For planning across multiple interconnected power systems (e.g., countries or regions).

### 10.1 Structure


Each system \(s\) has its own set of investment variables, operational sub-models, and RE targets. Systems are coupled through inter-system transmission links.

### 10.2 Inter-System Investment Variables


For each link \(l\) connecting system \(s_1\) (node \(n_1\)) to system \(s_2\) (node \(n_2\)):

\[
0 \leq I^{inter}_{y,l} \leq \bar{I}^{inter}_l \qquad \forall\, y,\, l \tag{INTER-1}
\]

**Cumulative limit:**

\[
\sum_{y=1}^{Y} I^{inter}_{y,l} \leq \bar{I}^{inter}_l \qquad \forall\, l \tag{INTER-2}
\]

**Symmetry for bidirectional links** (where link \(l'\) is the reverse of \(l\)):

\[
I^{inter}_{y,l} = I^{inter}_{y,l'} \qquad \forall\, y \tag{INTER-3}
\]

### 10.3 Inter-System Operational Coupling


**Julia function**: `add_inter_system_operational_coupling!(model, ext_vars, input)`

For each representative day, forward and reverse flow variables are created for each inter-system link:

\[
0 \leq F^{fwd}_{y,d,l,t} \leq \bar{F}^{inter,cum}_{y,l} \tag{INTER-4a}
\]

\[
0 \leq F^{rev}_{y,d,l,t} \leq \bar{F}^{inter,cum}_{y,l} \tag{INTER-4b}
\]

where the cumulative inter-system capacity is:

\[
\bar{F}^{inter,cum}_{y,l} = F^{exist}_l + \sum_{y'=1}^{y} I^{inter}_{y',l} \tag{INTER-5}
\]

**Border node injection:**

At the source node:

\[
\text{inj}_{s_1,n_1,t} = -F^{fwd}_{y,d,l,t} + (1 - \ell_l) \cdot F^{rev}_{y,d,l,t} \tag{INTER-6a}
\]

At the destination node:

\[
\text{inj}_{s_2,n_2,t} = (1 - \ell_l) \cdot F^{fwd}_{y,d,l,t} - F^{rev}_{y,d,l,t} \tag{INTER-6b}
\]

where \(\ell_l\) is the loss factor for link \(l\).

### 10.4 Multi-System Objective


**Julia function**: `build_multi_system_objective!(model, ext_vars, input)`

\[
\min \; Z^{multi} = \sum_{s} Z_s + \sum_{y=1}^{Y} \frac{1}{(1+r)^{y-1}} \sum_{l} c^{inter}_l \cdot I^{inter}_{y,l} + \sum_{y,d,l,t} c^{flow}_l \cdot d^{km}_l \left( F^{fwd}_{y,d,l,t} + F^{rev}_{y,d,l,t} \right) \cdot \frac{1}{(1+r)^{y-1}} \tag{MULTI-1}
\]

where \(Z_s\) is the per-system cost from Eq. (OBJ-1), \(c^{inter}_l\) is the inter-system investment cost per MW, and \(c^{flow}_l \cdot d^{km}_l\) is the distance-dependent operational flow cost.

---


## 11. Stochastic Extension


**Julia function**: `create_stochastic_master_problem(input::StochasticMasterInput)`

The stochastic formulation extends the deterministic Master Problem to handle uncertainty through scenario-based optimization.

### 11.1 Scenario Definition


Each scenario \(\omega\) has:

- A probability weight \(\pi_\omega\) where \(\sum_\omega \pi_\omega = 1\)
- A set of multipliers (`ScenarioMultipliers`) that scale costs and parameters:

| Multiplier | Description |
|------------|-------------|
| `invest_cost_renewables` | Scales RE investment costs |
| `invest_cost_conventional` | Scales conventional investment costs |
| `fuel_cost` | Scales fuel costs |
| `maintenance_cost` | Scales maintenance costs |
| `invest_cost_storage` | Scales storage investment costs |
| `invest_cost_transmission` | Scales transmission investment costs |
| `discount_rate` | Scenario-specific discount rate adjustment |
| `demand_growth` | Scenario-specific demand growth adjustment |

### 11.2 Two-Stage Structure


**First stage (here-and-now)**: Investment decisions \(I^{gen}_{y,g,n}\), \(I^{bat,P}_{y,b,n}\), \(I^{bat,E}_{y,b,n}\), \(I^{tr}_{y,i,j}\) are common across all scenarios. They use base (unmodified) investment costs.

**Second stage (wait-and-see)**: Operational costs are scenario-dependent. Modified generators and batteries are created by applying `ScenarioMultipliers` via `apply_scenario_multipliers()`.

### 11.3 Stochastic Objective


**Julia function**: `build_stochastic_objective!(model, vars, input, scenarios)`

\[
\min \; Z^{stoch} = \underbrace{\sum_{y=1}^{Y} \frac{C^{inv}_y}{(1+r)^{y-1}}}_{\text{First stage (deterministic)}} + \underbrace{\sum_{\omega} \pi_\omega \sum_{y=1}^{Y} \frac{C^{op}_{y,\omega}}{(1+r_\omega)^{y-1}}}_{\text{Second stage (expected cost)}} + C^{slack} \tag{STOCH-1}
\]

where \(r_\omega = r \cdot m^{discount}_\omega\) is the scenario-adjusted discount rate and \(C^{op}_{y,\omega}\) are the operational costs under scenario \(\omega\).

---


## 12. Solution Extraction


**Julia function**: `extract_master_solution(model, vars, input)`

After optimization, the following quantities are extracted:

| Output | Description | Computation |
|--------|-------------|-------------|
| `gen_investment[y][g]` | Generator investment per year and node (MW) | Direct variable values |
| `bat_power_investment[y][b]` | Battery power investment (MW) | Direct variable values |
| `bat_capacity_investment[y][b]` | Battery energy investment (MWh) | Direct variable values |
| `transfer_investment[y][(i,j)]` | Transmission investment (MW) | Direct variable values |
| `cumulative_gen_capacity[y][g]` | Cumulative gen capacity with degradation | Eq. (CUM-1) evaluated |
| `cumulative_bat_capacity[y][b]` | Cumulative battery energy capacity | Eq. (CUM-3) evaluated |
| `re_penetration_by_year[y]` | Achieved RE penetration ratio | `value(re_penetration_ratio[y])` |
| `total_investment_by_year[y]` | Total investment cost per year (\$) | Sum of all investment costs |
| `total_operational_cost_by_year[y]` | Total operational cost per year (\$) | From representative days |
| `gen_life_extension[y][g]` | Retirement status (1.0 = active, 0.0 = retired) | Age check against lifetime |

---


## 13. Infeasibility Diagnostics


**Julia function**: `diagnose_infeasibility(model, vars, input)`

If the model solves successfully but with non-zero slack, the diagnostics report which constraints required relaxation:

| Slack Variable | Indicates |
|----------------|-----------|
| \(s^{re}_y > 0\) | RE target cannot be met in year \(y\) |
| \(s^{bud}_y > 0\) | Investment budget exceeded in year \(y\) |
| \(s^{cap}_{y,n} > 0\) | Capacity inadequacy at node \(n\) in year \(y\) |

---


## 14. Implementation Summary

### Function Call Graph

```
create_master_problem(input)
  |-- calculate_target_ratios(input)             # RE-1
  |-- build_master_variables!(model, input)       # All variables (Sec. 3)
  |-- add_investment_constraints!()               # INV-1 to INV-3
  |-- add_budget_constraints!()                   # BUD-1
  |-- add_retirement_cascade_constraints!()       # No-op (RET handled in CUM)
  |-- add_capacity_adequacy_constraints!()        # CAP-1
  |-- add_transmission_symmetry_constraints!()    # TXN-1
  |-- add_representative_days_validation!()       # Sec. 8
  |     |-- select_representative_days()          # Sec. 8.1
  |     |-- create_day_operational_vars!()        # Sec. 8.2
  |     |-- add_day_operational_constraints!()    # Sec. 8.3
  |     +-- calculate_day_operational_cost()      # OBJ-5
  |-- add_re_target_constraints!()                # RE-2
  |-- add_re_increment_constraints!()             # RE-3
  +-- build_master_objective!()                   # OBJ-1

solve_with_npv_iteration(input)
  |-- create_master_problem()
  |-- optimize!()
  +-- loop:
        |-- get_units_with_negative_npv()         # Sec. 9.2
        |-- force_unit_retirements!()
        +-- optimize!()
```

---


## 15. MGA/SPORES: Near-Optimal Alternative Exploration


**Julia file**: `mga.jl`

MGA (Modeling to Generate Alternatives) [**[8]**](../reference/bibliography.md#ref8) and SPORES (Spatially-explicit Practically Optimal REsultS, Lombardi et al. 2020 [**[7]**](../reference/bibliography.md#ref7)) explore the space of near-optimal investment plans. In energy planning, multiple investment configurations can have similar total costs but very different compositions (e.g., more wind vs. more solar, distributed vs. centralized storage). These diverse alternatives inform policy decisions.

ESFEX implements **both methods** as distinct paths through the same machinery:

- **MGA** ($\S 15.2$–$\S 15.8$): the classical Hop-Skip-Jump (HSJ) loop. One diversity objective is applied $K$ times, each iteration penalising investment variables seen in the previous solutions.
- **SPORES** ($\S 15.9$–$\S 15.15$): a *menu* of distinct objectives. Each declared objective produces one alternative under the shared cost-slack constraint; the alternative count equals `len(objectives)`.

Both share the near-optimal constraint $Z \leq (1+\varepsilon) C^*$ and the per-alternative cost recovery; only the objective family differs.

### 15.1 Algorithm


```
1. Solve cost-optimal Master Problem → C*, x*₀
2. Add near-optimal constraint: total_cost ≤ (1 + ε) × C*
3. For k = 1, ..., K:
   a. Compute frequency scores from {x*₀, ..., x*_{k-1}}
   b. Set diversity-maximizing LP objective
   c. Solve → x*_k
```

**Key property**: The diversity objective is LP-compatible -- no binary variables are introduced.

### 15.2 Near-Optimal Constraint


After obtaining the cost-optimal solution with cost \(C^*\), a single constraint is added:

\[
Z \leq (1 + \varepsilon) \cdot C^* \tag{MGA-1}
\]

where \(\varepsilon\) is the `slack_fraction` (e.g., 0.05 for 5% cost increase).

### 15.3 Frequency Scoring


**Julia function**: `compute_frequency_scores(alternatives, input; investment_threshold=0.1)`

For each investment variable indexed by technology \(g\), node \(n\), and year \(y\), the frequency of investment across all previous alternatives is:

\[
\text{freq}_{g,n,y} = \frac{|\{k : I^{(k)}_{g,n,y} > \tau\}|}{K_{prev}} \tag{MGA-2}
\]

where \(\tau\) is the `investment_threshold` (MW) and \(K_{prev}\) is the number of alternatives found so far. The diversity score is:

\[
\sigma_{g,n,y} = 1 - 2 \cdot \text{freq}_{g,n,y} \tag{MGA-3}
\]

| Score range | Meaning | Effect |
|-------------|---------|--------|
| \(\sigma \approx +1\) | Rarely invested | Encourage |
| \(\sigma \approx 0\) | Invested ~50% of the time | Neutral |
| \(\sigma \approx -1\) | Always invested | Discourage |

Variables that have never appeared receive a default score of +1 (maximum encouragement).

### 15.4 Diversity Objective


**Julia function**: `set_spores_objective!(model, vars, input, frequency_scores)`

The diversity-maximizing objective replaces the cost objective:

\[
\max \sum_{y,g,n} \frac{\sigma_{g,n,y}}{\bar{I}_{g,n}} \cdot I^{gen}_{y,g,n} + \sum_{y,b,n} \frac{\sigma^{P}_{b,n,y}}{\bar{I}^{P}_{b,n}} \cdot I^{bat,P}_{y,b,n} + \sum_{y,b,n} \frac{\sigma^{E}_{b,n,y}}{\bar{I}^{E}_{b,n}} \cdot I^{bat,E}_{y,b,n} + \sum_{y,(i,j)} \frac{\sigma^{tr}_{i,j,y}}{\bar{I}^{tr}_i} \cdot I^{tr}_{y,i,j} \tag{MGA-4}
\]

Each investment variable is weighted by its diversity score and normalized by maximum investment capacity \(\bar{I}\). This normalization ensures technologies with different scales (e.g., 10 MW solar vs. 1000 MW wind) are treated comparably.

### 15.5 Cost Evaluation


After solving with the diversity objective, the actual system cost is recovered by evaluating the original cost expression:

\[
C_k = \text{value}(Z_{\text{original}}) \tag{MGA-5}
\]

Note: `objective_value(model)` returns the diversity objective, not the cost. The cost is obtained via `value(total_cost_expr)` where `total_cost_expr` was saved before modifying the objective.

### 15.6 Computational Cost


For \(K\) alternatives:

- **Solves**: \(K + 1\) (1 cost-optimal + \(K\) diversity)
- **Time**: Approximately \((K+1) \times T_{\text{master}}\)
- **Memory**: The JuMP model is reused; only the objective changes and one constraint is added
- **LP size**: Unchanged from the base Master Problem

### 15.7 Function Call Graph


```
run_mga_spores(input)
  |-- create_master_problem(input)        # Build base model
  |-- optimize!(model)                    # Step 0: cost-optimal
  |-- objective_function(model)           # Save cost expression
  |-- extract_master_solution(...)        # Alternative 0
  |-- @constraint(cost ≤ (1+ε)×C*)       # Near-optimal bound
  +-- loop k = 1..K:
        |-- compute_frequency_scores()    # MGA-2, MGA-3
        |-- set_spores_objective!()       # MGA-4 (replaces obj)
        |-- optimize!(model)              # Diversity solve
        |-- value(total_cost_expr)        # MGA-5 (actual cost)
        +-- extract_master_solution(...)  # Alternative k
```

### 15.8 Configuration


| Parameter | Default | Description |
|-----------|---------|-------------|
| `mga.enabled` | `false` | Enable MGA / SPORES |
| `mga.method` | `"mga"` | Generation method: `"mga"` (HSJ loop, $\S 15.2$–$\S 15.7$) or `"spores"` (per-objective sweep, $\S 15.9$–$\S 15.15$) |
| `mga.num_alternatives` | 10 | Number of diversity alternatives \(K\). **Used only when `method = "mga"`** — ignored under SPORES (the count equals `len(objectives)`) |
| `mga.slack_fraction` | 0.05 | Near-optimal slack \(\varepsilon\) — shared by both methods |
| `mga.investment_threshold` | 0.1 MW | Threshold \(\tau\) for frequency counting. Used by HSJ and by the SPORES `hsj_diversity` objective; ignored by the other SPORES objectives |
| `mga.objectives` | `[]` | SPORES objective menu (list of [`SporesObjective`](../api/config-schema.md#sporesobjective)). **Required when `method = "spores"`** — ignored under MGA |

---

## 15. (continued) SPORES: per-objective alternative generation

### 15.9 Overview


**Julia entry point**: `run_spores(input; objectives, slack_fraction, …)`

SPORES replaces the HSJ frequency loop with a *menu* of distinct LP objectives. Each entry in `objectives` produces one alternative under the same cost-slack constraint \(Z \leq (1+\varepsilon) C^*\). ESFEX ships five canonical objectives, the four "classical" SPORES objectives from Lombardi et al. (2020) plus the HSJ score retained as a special case:

| Symbol | Sense | Section |
|--------|-------|---------|
| `:hsj_diversity` | \(\max\) | § 15.4 (same as MGA — reusable inside a SPORES sweep) |
| `:min_total_build` | \(\min\) | § 15.10 |
| `:max_tech_equity` | \(\min\) (min-max) | § 15.11 |
| `:max_regional_equity` | \(\min\) (min-max) | § 15.12 |
| `:evolutionary_dist` | \(\max\) | § 15.13 |

All objectives are linear; auxiliary variables introduced by the min-max equity objectives and by the L1-linearised distance objective are tracked in `model[:_spores_objective_aux]` and deleted between sweep iterations (see § 15.14) so the JuMP model never accumulates dead variables across the sweep.

### 15.10 Minimum total build objective


**Julia function**: `set_min_build_objective!(model, vars, input)`

Selects the *smallest* near-optimal portfolio. Useful when the cost slack admits a deployment-light plan that meets the same demand and renewable-energy targets:

\[
\min \sum_{y,t,n} I^{tech}_{y,t,n} + \sum_{y,b,n} I^{bat,P}_{y,b,n} + \sum_{y,(i,j)} I^{tr}_{y,(i,j)} \tag{SPORES-1}
\]

where the investment-period gating from § 16 still applies (the sums only run over the period-start years \(y_{\mathrm{idx}} \in \{1, 1+y_{pp}, 1+2 y_{pp}, \ldots\}\)). Battery *energy* investments are deliberately excluded from the MW sum because the units (MWh vs MW) do not combine cleanly; the energy variable is implicitly constrained through the per-tech duration limit.

### 15.11 Technology equity objective


**Julia function**: `set_tech_equity_objective!(model, vars, input)`

Minimises the largest per-technology share of the build (a min-max equity formulation, sometimes called Gini-min in the SPORES literature):

\[
\min \; M \tag{SPORES-2a}
\]
\[
\text{s.t.}\quad \sum_{y, n} \frac{I^{tech}_{y,t,n}}{\bar{I}_{t,n}} \;\leq\; M \qquad \forall\, t \in \mathcal{T} \tag{SPORES-2b}
\]

Investments are normalised by the per-tech-per-node investment cap \(\bar{I}_{t,n}\) so technologies with different scales (10 MW vs 1000 MW) compete on the same footing. The auxiliary scalar \(M \geq 0\) and the \(|\mathcal{T}|\) per-tech constraints are anonymous (no `base_name`) and stashed in the aux registry. Technologies whose investment cap is zero everywhere contribute an empty sum that reduces to the trivial constraint \(0 \leq M\).

### 15.12 Regional equity objective


**Julia function**: `set_regional_equity_objective!(model, vars, input)`

The spatially-explicit twin of § 15.11 — minimises the largest per-node share of the build. This is the canonical SPORES objective from which the name *spatially-explicit* derives:

\[
\min \; M \tag{SPORES-3a}
\]
\[
\text{s.t.}\quad \sum_{y, t} \frac{I^{tech}_{y,t,n}}{\bar{I}_{t,n}}
\;+\; \sum_{y, b} \frac{I^{bat,P}_{y,b,n}}{\bar{I}^{P}_{b,n}}
\;\leq\; M \qquad \forall\, n \in \mathcal{N} \tag{SPORES-3b}
\]

Battery power investments are included because they materially affect spatial siting; battery energy is omitted for the same unit-mixing reason as in (SPORES-1).

### 15.13 Evolutionary distance objective


**Julia function**: `set_evolutionary_distance_objective!(model, vars, input, reference_solution)`

Maximises the L1 distance (normalised) from a reference solution \(x_{\mathrm{ref}}\) — typically the cost-optimal plan \(x_0^*\). Used to surface the *maximally different* feasible plan when several visually similar near-optima exist:

\[
\max \sum_{y,t,n} \frac{|I^{tech}_{y,t,n} - I^{tech,\mathrm{ref}}_{y,t,n}|}{\bar{I}_{t,n}}
\;+\; \sum_{y,b,n} \frac{|I^{bat,P}_{y,b,n} - I^{bat,P,\mathrm{ref}}_{y,b,n}|}{\bar{I}^{P}_{b,n}} \tag{SPORES-4}
\]

The L1 norm is linearised via positive / negative deviation auxiliaries:

\[
I^{tech}_{y,t,n} - I^{tech,\mathrm{ref}}_{y,t,n} \;=\; d^{+}_{y,t,n} - d^{-}_{y,t,n}, \quad d^{+}_{y,t,n},\, d^{-}_{y,t,n} \geq 0 \tag{SPORES-4a}
\]

so the objective becomes \(\max \sum (d^+ + d^-)/\bar{I}\). The Euclidean L2 distance would force the model out of LP into QP and is therefore not used.

### 15.14 Dispatcher and aux-variable cleanup


**Julia function**: `apply_spores_objective!(model, vars, input, objective::Symbol; frequency_scores = nothing, reference_solution = nothing)`

Routes a SPORES objective symbol to the matching `set_*_objective!` function and validates required kwargs:

- `:hsj_diversity` requires `frequency_scores` (a `Dict{String, Float64}`).
- `:evolutionary_dist` requires `reference_solution` (a `MasterProblemResult`).
- The other three need neither.

Each call begins with `_clear_spores_aux!(model)` which deletes every variable / constraint installed by the previous objective. Aux references live in `model[:_spores_objective_aux]` as a `Vector{Any}`; `JuMP.delete` is wrapped in `try` / `catch` to stay idempotent across repeated cleanups. This is what allows the sweep loop (§ 15.15) to re-use a single model across all objectives without unbounded growth.

### 15.15 SPORES sweep loop


**Julia function**: `run_spores(input; objectives, slack_fraction, use_representative_days, investment_threshold)`

```
run_spores(input, objectives = [:min_total_build, :max_tech_equity, …])
  |-- create_master_problem(input)        # Build base model
  |-- optimize!(model)                    # Step 0: cost-optimal
  |-- objective_function(model)           # Save cost expression
  |-- extract_master_solution(...)        # Reference / alt 0
  |-- @constraint(cost ≤ (1+ε)×C*)       # Near-optimal envelope
  +-- for k = 1..len(objectives):
        |-- _clear_spores_aux!(model)     # Drop previous aux
        |-- apply_spores_objective!(...)  # Install objective k
        |-- optimize!(model)              # Solve under cost cap
        |-- value(total_cost_expr)        # MGA-5 (actual cost)
        +-- extract_master_solution(...)  # Alternative k
```

The result is an `MGAResult` whose `alternatives[1]` is the cost-optimal seed, `alternatives[2:end]` are the SPORES solutions, and `objective_labels[k]` carries the symbol that produced `alternatives[k+1]`.

### 15.16 When to use MGA vs SPORES


| Use case | Use MGA | Use SPORES |
|----------|---------|------------|
| "Show me K alternatives, surprise me" | ✅ | — |
| "Show me the smallest near-optimal plan" | — | ✅ (`min_total_build`) |
| "How spatially flexible is the optimum?" | — | ✅ (`max_regional_equity`) |
| "Quantify the technology-substitution envelope" | indirect | ✅ (`max_tech_equity`) |
| Need *named* alternatives for policy discussion | — | ✅ |
| Need a large set (K = 20+) for statistical robustness | ✅ | ❌ (limited by objective count) |
| Cost cap is the only constraint | ✅ | ✅ |

In practice, SPORES is preferable when each alternative needs to *answer a specific question*; MGA is preferable when the goal is to *map the breadth* of the near-optimal space with a larger sample.

---

### Solver Configuration

The Master Problem uses HiGHS with the following settings:

| Parameter | Default | Julia field |
|-----------|---------|-------------|
| Threads | 4 | `input.threads` |
| Time limit | 3600 s | `input.time_limit` |
| MIP gap | 0.01 | `input.gap` |
| Verbose | false | `input.verbose` |

Since the formulation is a pure LP (all continuous variables, age-based retirement evaluated at construction time), the MIP gap setting is effectively unused. The solver employs the simplex or interior point method for LP.

---


## 16. Per-Technology Investment Model


Investment decisions can be modeled at the **technology** level rather than per-generator. This aggregates investment capacity across all generators of the same type, enabling more realistic technology-level constraints.

### 16.1 Technology Configuration


Technologies are defined in the YAML configuration:

```yaml
technologies:
  - name: "Solar PV"
    type: "Renewable"
    generators: ["Solar_Farm_1", "Solar_Farm_2"]
    invest_min_mw: 0.0
    invest_max_mw: 500.0
    investment_cost_per_mw: 800000.0
    fixed_om_per_mw: 12000.0
    lifetime_years: 25

battery_technologies:
  - name: "Li-Ion Storage"
    batteries: ["Battery_1", "Battery_2"]
    invest_min_power_mw: 0.0
    invest_max_power_mw: 200.0
    invest_max_energy_mwh: 800.0
    power_cost_per_mw: 150000.0
    energy_cost_per_mwh: 200000.0
    lifetime_years: 15
```

### 16.2 Technology-Level Variables


| Variable | Domain | Units | Description |
|----------|--------|-------|-------------|
| \(I^{tech}_{y,k}\) | \(\mathbb{R}_+\) | MW | Total technology \(k\) investment in year \(y\) |
| \(I^{bat,tech,P}_{y,k}\) | \(\mathbb{R}_+\) | MW | Battery technology \(k\) power investment |
| \(I^{bat,tech,E}_{y,k}\) | \(\mathbb{R}_+\) | MWh | Battery technology \(k\) energy investment |

### 16.3 Cumulative Capacity


The cumulative installed capacity for technology \(k\) at year \(y\) includes both existing capacity and all investments up to year \(y\), minus retirements:

\[
\bar{P}^{tech}_{k,y} = \bar{P}^{exist}_{k} + \sum_{y'=1}^{y} I^{tech}_{y',k} - \sum_{y'=1}^{y} R^{tech}_{y',k}
\]

where \(R^{tech}_{y',k}\) is retirement computed by age-based expiry.

### 16.4 Virtual Generators


Technology-level investments create **virtual generators** for operational dispatch. After the master problem solves, the runner:

1. Computes cumulative investment per technology per year
2. Creates synthetic generator entries with `rated_power = cumulative_investment`
3. Passes these virtual generators to the operational dispatch adapter
4. Virtual generators use the same availability profiles and cost parameters as the technology definition

This bridge between strategic (technology-level) and operational (generator-level) modeling ensures investment decisions are reflected in detailed dispatch simulation.

---


## 17. Piecewise-Linear Cost Curves in Investment


Investment costs can vary with scale using piecewise-linear (PWL) cost curves. This captures economies of scale (decreasing marginal cost for larger projects) or diseconomies (increasing cost for difficult sites).

### 17.1 Cost Curve Structure


Each technology can define cost curve blocks:

```yaml
technologies:
  - name: "Wind Onshore"
    cost_curve:
      blocks:
        - power_mw: 100.0
          cost_per_mw: 1200000.0   # First 100 MW at $1.2M/MW
        - power_mw: 200.0
          cost_per_mw: 1000000.0   # Next 200 MW at $1.0M/MW
        - power_mw: 200.0
          cost_per_mw: 900000.0    # Next 200 MW at $0.9M/MW
```

### 17.2 Formulation


The total investment cost for technology \(k\) in year \(y\) is:

\[
C^{inv}_{k,y} = \sum_{s=1}^{S} c^{inv}_{k,s} \cdot \delta^{inv}_{k,y,s}
\]

where:
- \(c^{inv}_{k,s}\) is the cost per MW in segment \(s\)
- \(\delta^{inv}_{k,y,s}\) is the investment in segment \(s\) (bounded by segment width)

The total investment equals the sum of segments:

\[
I^{tech}_{y,k} = \sum_{s=1}^{S} \delta^{inv}_{k,y,s}, \quad 0 \leq \delta^{inv}_{k,y,s} \leq \bar{\delta}_{k,s}
\]

When costs are non-increasing (\(c^{inv}_{k,1} \geq c^{inv}_{k,2} \geq \ldots\)), the formulation is convex and the LP solver naturally fills cheaper segments first without requiring binary variables.

---

## References

The capacity expansion formulation draws on the general framework for generation expansion planning reviewed by Koltsaklis and Dagoumas [**[24]**](../reference/bibliography.md#ref24). The importance of embedding operational flexibility constraints within planning models is analyzed by Palmintier and Webster [**[25]**](../reference/bibliography.md#ref25) and Poncelet et al. [**[26]**](../reference/bibliography.md#ref26). The interaction between unit commitment constraints and generation expansion is studied by Schwele et al. [**[35]**](../reference/bibliography.md#ref35), which motivates ESFEX's two-stage decomposition approach. The role of operational detail in planning models with high variable RE shares is reviewed by Helistö et al. [**[36]**](../reference/bibliography.md#ref36). Representative day selection and time series aggregation methods follow Kotzur et al. [**[22]**](../reference/bibliography.md#ref22) and Nahmmacher et al. [**[37]**](../reference/bibliography.md#ref37); for a broader review see Hoffmann et al. [**[23]**](../reference/bibliography.md#ref23). The MGA/SPORES methodology for near-optimal space exploration follows Lombardi et al. [**[7]**](../reference/bibliography.md#ref7) and DeCarolis [**[8]**](../reference/bibliography.md#ref8); see also Neumann and Brown [**[38]**](../reference/bibliography.md#ref38). The optimization is formulated via JuMP [**[20]**](../reference/bibliography.md#ref20) and solved with HiGHS [**[21]**](../reference/bibliography.md#ref21) by default.

See the [full bibliography](../reference/bibliography.md) for complete citation details.
