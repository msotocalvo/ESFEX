# Operational Dispatch


The operational dispatch determines the hourly generation schedule that meets demand at minimum cost while respecting all physical and policy constraints. It is implemented in `power_system.jl` and solved for each temporal window within each planning year. The window size is controlled by `rolling_horizon_hours`.

Three operating modes are available:

- **Economic dispatch (ED):** Generator commitment is fixed to 1; the problem is a continuous LP.
- **Unit commitment (UC):** Generator on/off status is a binary decision variable, yielding a MIP with minimum up/down times and startup costs.
- **Development:** Identical to ED but includes investment decision variables for capacity expansion screening.

---


## 1. Sets and Indices

| Symbol | Description | Julia Range |
|--------|-------------|-------------|
| \(g \in \mathcal{G}\) | Generators | `1:n_gen` |
| \(b \in \mathcal{B}\) | Batteries (storage systems) | `1:n_bat` |
| \(n \in \mathcal{N}\) | Buses (electrical nodes) | `1:n_bus` |
| \(t \in \mathcal{T}\) | Time steps (hours) | `1:hours` |
| \(s \in \mathcal{S}\) | Demand sectors | `keys(sectoral_demand)` |
| \(\ell \in \mathcal{L}\) | Transmission lines (physical) | `1:n_line` |
| \(c \in \mathcal{C}\) | Independent network cycles | `1:n_cycle` |
| \(k \in \mathcal{K}\) | AC/DC converters | `1:n_acdc` |
| \(f \in \mathcal{F}\) | Frequency converters | `1:n_freq` |

The mapping from bus to geographic node is given by `bus_to_node[n]`, written as \(n_i\). Each bus carries a `demand_fraction` \(\alpha_n\) of its parent node's demand.

---


## 2. Decision Variables


### 2.1 Generator Variables

| Variable | Indices | Domain | Units | Description |
|----------|---------|--------|-------|-------------|
| \(p_{g,n,t}\) | \(g,n,t\) | \(\geq 0\) | MW | Generator output (`gen_output`) |
| \(u_{g,n,t}\) | \(g,n,t\) | \(\{0,1\}\) (UC) or \(=1\) (ED) | -- | Generator status (`gen_status`) |
| \(v_{g,n,t}\) | \(g,n,t\) | \([0,1]\) | -- | Startup indicator (`gen_startup`); UC mode only |
| \(\hat{P}^{\text{inv}}_{g,n}\) | \(g,n\) | \(\geq 0\) | MW | Generator investment (`gen_investment`); development mode only |

### 2.2 Battery Variables

| Variable | Indices | Domain | Units | Description |
|----------|---------|--------|-------|-------------|
| \(p^{\text{ch}}_{b,n,t}\) | \(b,n,t\) | \(\geq 0\) | MW | Charge power (`bat_charge`) |
| \(p^{\text{dis}}_{b,n,t}\) | \(b,n,t\) | \(\geq 0\) | MW | Discharge power (`bat_discharge`) |
| \(E_{b,n,t}\) | \(b,n,t\) | \(\geq 0\) | MWh | State of charge (`bat_soc`); index 1 = initial |
| \(\delta^{\text{ch}}_{b,n,t}\) | \(b,n,t\) | \([0,1]\) | -- | Charge status for mutual exclusivity (`bat_charge_status`) |
| \(\sigma_{b,n,t}\) | \(b,n,t\) | \(\geq 0\) | MWh | SOC violation slack (`soc_violation`) |
| \(\text{spill}_{b,n,t}\) | \(b,n,t\) | \(\geq 0\) | MW | Battery spillage (`bat_spillage`); optional per battery |

### 2.3 Network Variables

| Variable | Indices | Domain | Units | Description |
|----------|---------|--------|-------|-------------|
| \(f_{\ell,t}\) | \(\ell,t\) | free | MW | Power flow per physical line (`power_flow`) |
| \(\theta_{n,t}\) | \(n,t\) | free | rad | Voltage angle (`voltage_angle`) |
| \(\mu_{i,j,t}\) | \(i,j,t\) | \(\geq 0\) | MW | Transfer margin violation slack (`transfer_margin`) |

### 2.4 Converter Variables

| Variable | Indices | Domain | Units | Description |
|----------|---------|--------|-------|-------------|
| \(p^{\text{rect}}_{k,t}\) | \(k,t\) | \(\geq 0\) | MW | AC-to-DC rectification power (`acdc_rectify`) |
| \(p^{\text{inv}}_{k,t}\) | \(k,t\) | \(\geq 0\) | MW | DC-to-AC inversion power (`acdc_invert`) |
| \(p^{A \to B}_{f,t}\) | \(f,t\) | \(\geq 0\) | MW | Frequency converter A-to-B flow (`freq_flow_a_to_b`) |
| \(p^{B \to A}_{f,t}\) | \(f,t\) | \(\geq 0\) | MW | Frequency converter B-to-A flow (`freq_flow_b_to_a`) |

### 2.5 Reserve and Reliability Variables

| Variable | Indices | Domain | Units | Description |
|----------|---------|--------|-------|-------------|
| \(r^{\text{sta}}_{n,t}\) | \(n,t\) | \(\geq 0\) | MW | Static reserve provision (`reserve_static`) |
| \(r^{\text{dyn}}_{n,t}\) | \(n,t\) | \(\geq 0\) | MW | Dynamic reserve provision (`reserve_dynamic`) |
| \(\lambda^{\text{sta}}_{n,t}\) | \(n,t\) | \(\geq 0\) | MW | Static reserve shortage (`reserve_static_loss`) |
| \(\lambda^{\text{dyn}}_{n,t}\) | \(n,t\) | \(\geq 0\) | MW | Dynamic reserve shortage (`reserve_dynamic_loss`) |
| \(\text{LS}_{n,t}\) | \(n,t\) | \(\geq 0\) | MW | Load shedding (`load_shed`) |
| \(\text{CU}_{n,t}\) | \(n,t\) | \(\geq 0\) | MW | Renewable curtailment (`curtailment`) |
| \(\phi_{n,t}\) | \(n,t\) | \(\geq 0\) | MW | RE penetration loss slack (`fre_penetration_loss`) |
| \(H_t\) | \(t\) | \(\geq 0\) | MW-s | Inertia shortfall (`loss_of_inertia`) |

### 2.6 Emissions Variables

| Variable | Indices | Domain | Units | Description |
|----------|---------|--------|-------|-------------|
| \(\text{CO2}_{n,t}\) | \(n,t\) | \(\geq 0\) | tonnes | CO2 emissions at bus and hour (`co2_emissions`) |
| \(\xi^{\text{CO2}}\) | scalar | \(\geq 0\) | tonnes | CO2 budget violation slack (`co2_budget_violation`) |

### 2.7 EV Fleet Variables

| Variable | Indices | Domain | Units | Description |
|----------|---------|--------|-------|-------------|
| \(p^{\text{ev,ch}}_{n,t}\) | \(n,t\) | \(\geq 0\) | MW | EV charging power (`ev_charging`) |
| \(p^{\text{v2g}}_{n,t}\) | \(n,t\) | \(\geq 0\) | MW | Vehicle-to-grid power (`ev_v2g`) |
| \(E^{\text{ev}}_{n,t}\) | \(n,t\) | \(\geq 0\) | MWh | EV fleet SOC (`ev_soc`); index 1 = initial |
| \(\ell^{\text{ev}}_{n,t}\) | \(n,t\) | \(\geq 0\) | MW | EV charging loss (`ev_loss`) |
| \(\delta^{\text{ev}}_{n,t}\) | \(n,t\) | \([0,1]\) | -- | EV charge/V2G mutex status (`ev_charge_status`) |

### 2.8 Sectoral Demand Variables

| Variable | Indices | Domain | Units | Description |
|----------|---------|--------|-------|-------------|
| \(\text{LOL}_{s,n,t}\) | \(s,n,t\) | \(\geq 0\) | MW | Per-sector load shedding (`loss_of_load_sectoral`) |
| \(\text{FC}_{s,n,t}\) | \(s,n,t\) | \(\geq 0\) | MW | Flexible demand curtailed (`flexible_demand_curtailed`) |
| \(\Delta_{s,n,t,t'}\) | \(s,n,t,t'\) | \(\geq 0\) | MW | Demand shifted from \(t\) to \(t'\) (`demand_shift`) |

### 2.9 Other Variables

| Variable | Indices | Domain | Units | Description |
|----------|---------|--------|-------|-------------|
| \(p^{\text{elz}}_{n,t}\) | \(n,t\) | \(\geq 0\) | MW | Electrolyzer power consumption (`electrolyzer_power`) |
| \(\text{RC}_{n,t}\) | \(n,t\) | \(\geq 0\) | MW | Rooftop solar curtailment (`rooftop_curtailment`) |

---


## 3. Objective Function


The total system cost over the time horizon is minimized:

\[
\min \; Z = Z^{\text{op}} + Z^{\text{start}} + Z^{\text{bat}} + Z^{\text{pen}} + Z^{\text{CO2}} + Z^{\text{inv}} + Z^{\text{flex}} + Z^{\text{conv}} + Z^{\text{elz}} + Z^{\text{ret}} + Z^{\text{shift}} + Z^{\text{npv}} \tag{OBJ}
\]

Implemented in `build_objective!()`.

### 3.1 Operational Costs

\[
Z^{\text{op}} = \sum_{g,n,t} \left( c^{\text{fuel}}_{g,n} + c^{\text{fixed}}_{g,n} + c^{\text{maint}}_{g,n} \right) p_{g,n,t} \tag{OBJ-1}
\]

where \(c^{\text{fuel}}_{g,n}\), \(c^{\text{fixed}}_{g,n}\), and \(c^{\text{maint}}_{g,n}\) are per-MWh fuel, fixed O&M, and maintenance costs respectively. The sums are restricted to buses where \(\bar{P}_{g,n} > 0\).

#### 3.1.1 Piecewise-Linear (PWL) Fuel Cost Decomposition

When a generator has a `fuel_cost_curve` configured (see [Configuration Reference](../reference/config-reference.md#costcurveconfig)), the flat fuel cost \(c^{\text{fuel}}_{g,n}\) is replaced by a piecewise-linear (PWL) cost formulation that decomposes the generator output into segments with increasing marginal costs.

**Segment variables.** The generator output is decomposed into \(K\) segments:

\[
p_{g,n,t} = \sum_{k=1}^{K} p^{(k)}_{g,n,t} \tag{PWL-1}
\]

Each segment is bounded by the segment's fraction of rated capacity:

\[
0 \leq p^{(k)}_{g,n,t} \leq f_k \cdot \bar{P}^{\text{eff}}_{g,n} \quad \forall k \in \{1, \ldots, K\} \tag{PWL-2}
\]

where \(f_k\) is the fraction of rated capacity assigned to segment \(k\) (with \(\sum_k f_k = 1\)) and \(\bar{P}^{\text{eff}}_{g,n}\) is the effective rated capacity at bus \(n\).

**Segment costs.** Each segment has a marginal cost \(c_k\) ($/MWh), and the total fuel cost for the generator replaces the flat-cost term in Eq. (OBJ-1):

\[
Z^{\text{op,pwl}}_{g,n} = \sum_{t} \sum_{k=1}^{K} c_k \cdot p^{(k)}_{g,n,t} \tag{PWL-3}
\]

**Convexity.** The marginal costs must be non-decreasing (\(c_1 \leq c_2 \leq \cdots \leq c_K\)). This ensures that the LP solver fills the cheaper segments first, which is necessary for the decomposition to correctly represent the cost curve without requiring explicit ordering constraints.

**Flat curve special case.** When `curve_type = "flat"` (or no curve is configured), \(K = 1\), \(f_1 = 1\), and \(c_1 = c^{\text{fuel}}_{g,n}\). The formulation reduces exactly to the original Eq. (OBJ-1) with no additional variables or constraints.

**Stepwise curves.** Each block in the `blocks` list directly maps to a segment: \(f_k = \text{block}_k.\text{fraction}\), \(c_k = \text{block}_k.\text{price}\).

**Linear and exponential curves.** The continuous curve is approximated by `num_segments` equal-width segments. The marginal cost for each segment is evaluated at the segment midpoint.

!!! note "Battery discharge cost curves"
    The same PWL decomposition is available for battery discharge via `discharge_cost_curve`. In this case, the discharge power \(p^{\text{dis}}_{b,n,t}\) is decomposed into segments with non-decreasing marginal costs, and the total discharge cost replaces the flat maintenance cost in Eq. (OBJ-3):

    \[
    p^{\text{dis}}_{b,n,t} = \sum_{k=1}^{K} p^{\text{dis},(k)}_{b,n,t}, \quad Z^{\text{bat,pwl}}_{b,n} = \sum_{t} \sum_{k=1}^{K} c^{\text{dis}}_k \cdot p^{\text{dis},(k)}_{b,n,t}
    \]

### 3.2 Startup Costs (UC mode only)

\[
Z^{\text{start}} = \sum_{g,n,t} c^{\text{su}}_{g,n} \, v_{g,n,t} \tag{OBJ-2}
\]

### 3.3 Battery Maintenance Cost

\[
Z^{\text{bat}} = \sum_{b,n,t} c^{\text{bat,maint}}_{b,n} \left( p^{\text{ch}}_{b,n,t} + p^{\text{dis}}_{b,n,t} \right) \tag{OBJ-3}
\]

### 3.3b Battery Throughput Degradation Cost

\[
Z^{\text{bat,thr}} = \sum_{b,n,t} c^{\text{thr}}_{b,n} \cdot p^{\text{dis}}_{b,n,t} \tag{OBJ-3b}
\]

Represents the wear cost of cycling energy through the battery, modelling the economic impact of throughput-related degradation on cell lifetime. The cost is applied to **discharge only** (each MWh discharged represents one full unit of cycled energy). Typical values range from 2 $/MWh (flow batteries) to 15 $/MWh (lead-acid), with Li-ion around 5 $/MWh.

- \(c^{\text{thr}}_{b,n}\) — throughput degradation cost ($/MWh discharged), from `bat.throughput_degradation_cost[n]`
- Defaults to 0 when not specified (backward-compatible)

### 3.4 Penalty Costs

\[
Z^{\text{pen}} = \underbrace{C^{\text{VOLL}} \sum_{n,t} \text{LS}_{n,t}}_{\text{load shedding}} + \underbrace{C^{\text{CU}} \sum_{n,t} \text{CU}_{n,t}}_{\text{curtailment}} + \underbrace{C^{\text{r,sta}} \sum_{n,t} \lambda^{\text{sta}}_{n,t}}_{\text{static reserve}} + \underbrace{C^{\text{r,dyn}} \sum_{n,t} \lambda^{\text{dyn}}_{n,t}}_{\text{dynamic reserve}} \tag{OBJ-4a}
\]

\[
+ \underbrace{C^{\text{ine}} \sum_t H_t}_{\text{inertia}} + \underbrace{C^{\text{ev}} \sum_{n,t} \ell^{\text{ev}}_{n,t}}_{\text{EV loss}} + \underbrace{C^{\text{FRE}} \sum_{n,t} \phi_{n,t}}_{\text{RE target}} + \underbrace{C^{\text{SOC}} \sum_{b,n,t} \sigma_{b,n,t}}_{\text{SOC violation}} \tag{OBJ-4b}
\]

\[
+ \underbrace{C^{\text{TM}} \sum_{i,j,t} \mu_{i,j,t}}_{\text{transfer margin}} + \underbrace{C^{\text{CO2,bud}} \, \xi^{\text{CO2}}}_{\text{CO2 budget violation}} + \underbrace{C^{\text{RC}} \sum_{n,t} \text{RC}_{n,t}}_{\text{rooftop curtailment}} \tag{OBJ-4c}
\]

### 3.5 Sectoral Load Shedding Penalties

\[
Z^{\text{sec}} = \sum_{s,n,t} C^{\text{VOLL}} \cdot \kappa_s \cdot \text{LOL}_{s,n,t} \tag{OBJ-5}
\]

where \(\kappa_s\) is the criticality weight for sector \(s\). Higher criticality sectors incur a higher penalty per unit of load shed, causing the optimizer to shed lower-criticality sectors first.

### 3.6 CO2 Emission Cost

\[
Z^{\text{CO2}} = C^{\text{CO2}} \sum_{g,n,t} e_{\text{fuel}(g)} \, p_{g,n,t} \tag{OBJ-6}
\]

where \(e_f\) is the emission factor (tonnes CO2/MWh) for the fuel type of generator \(g\).

### 3.7 Flexible Demand Benefit (subtracted)

\[
Z^{\text{flex}} = - \sum_{s,n,t} \pi_t \cdot \beta^{\text{flex}} \cdot \text{FC}_{s,n,t} \tag{OBJ-7}
\]

where \(\pi_t\) is the electricity price at hour \(t\) and \(\beta^{\text{flex}}\) is the `flexible_demand_benefit_ratio`. This term is subtracted from cost because reducing flexible demand provides economic benefit.

### 3.8 V2G Compensation (subtracted)

\[
Z^{\text{V2G}} = - \sum_{n,t} \pi_t \, p^{\text{v2g}}_{n,t} \tag{OBJ-8}
\]

Time-varying compensation using the electricity price signal.

### 3.9 Battery Spillage Cost

\[
Z^{\text{spill}} = \sum_{b,n,t} \pi_t \cdot \text{spill}_{b,n,t} \tag{OBJ-9}
\]

Opportunity cost of energy discharged from batteries without grid injection.

### 3.10 Investment Costs (development mode only)

\[
Z^{\text{inv}} = \sum_{g,n} c^{\text{inv}}_g \hat{P}^{\text{inv}}_{g,n} + \sum_{b,n} \left( c^{\text{inv,P}}_b \hat{P}^{\text{inv,P}}_{b,n} + c^{\text{inv,E}}_b \hat{E}^{\text{inv}}_{b,n} \right) \tag{OBJ-10}
\]

### 3.11 Converter Costs

\[
Z^{\text{conv}} = \sum_{k,t} c^{\text{var}}_k \left( p^{\text{rect}}_{k,t} + p^{\text{inv}}_{k,t} \right) + \sum_{f,t} c^{\text{var}}_f \left( p^{A \to B}_{f,t} + p^{B \to A}_{f,t} \right) \tag{OBJ-11}
\]

### 3.12 Electrolyzer Costs

\[
Z^{\text{elz}} = \sum_{n,t} c^{\text{elz,var}}_n \, p^{\text{elz}}_{n,t} + \sum_n \bar{P}^{\text{elz}}_n \, c^{\text{elz,fix}}_n \cdot T \tag{OBJ-12}
\]

### 3.13 Demand Shifting Cost

\[
Z^{\text{shift}} = \sum_{s,n,t,t'} |t - t'| \cdot \gamma^{\text{shift}} \cdot \Delta_{s,n,t,t'} \tag{OBJ-13}
\]

where \(\gamma^{\text{shift}}\) is the `demand_shift_cost_rate`. The cost increases with temporal distance.

### 3.14 Delayed Retirement Penalty

\[
Z^{\text{ret}} = C^{\text{delay}} \sum_{g,n} \bar{P}^{\text{orig}}_{g,n} \, d_{g,n} \tag{OBJ-14}
\]

where \(d_{g,n} \in \{0,1\}\) is the delay retirement binary variable and \(\bar{P}^{\text{orig}}_{g,n}\) is the original rated capacity.

### 3.15 NPV-Based Forced Replacement Penalty

\[
Z^{\text{npv}} = \sum_{g,n} \left( C^{\text{decom}}_{g,n} \cdot \bar{P}_{g,n} + C^{\text{npv}}_{g,n} \cdot \bar{P}_{g,n} \cdot 0.1 \right) \cdot r_{g,n} \tag{OBJ-15}
\]

where \(r_{g,n}\) is the forced replacement variable (continuous) and costs are capped at 100,000 $/MW (decommissioning) and 50,000 $/MW (NPV penalty).

!!! note "Complete objective"
    The full objective assembled in `build_objective!()` is:

    \[
    \min \; Z^{\text{op}} + Z^{\text{start}} + Z^{\text{bat}} + Z^{\text{pen}} + Z^{\text{sec}} + Z^{\text{CO2}} + Z^{\text{inv}} + Z^{\text{conv}} + Z^{\text{elz}} + Z^{\text{ret}} + Z^{\text{shift}} + Z^{\text{npv}} + Z^{\text{spill}} - Z^{\text{flex}} - Z^{\text{V2G}}
    \]

---


## 4. Constraint Families


### Generator Constraints

Implemented in `add_generator_constraints!()`.

#### GEN-1: Generator Capacity Limits

**Renewable generators** are limited by the availability factor \(a_{g,n,t} \in [0,1]\):

\[
p_{g,n,t} \leq \left( \bar{P}^{\text{eff}}_{g,n} + \hat{P}^{\text{inv}}_{g,n} + \bar{P}^{\text{delay}}_{g,n} \right) \cdot a_{g,n,t} \quad \forall g \in \mathcal{G}^{\text{RE}}, n, t \tag{GEN-1a}
\]

**Non-renewable generators** are limited by total capacity and commitment status:

\[
p_{g,n,t} \leq \bar{P}^{\text{eff}}_{g,n} + \hat{P}^{\text{inv}}_{g,n} + \bar{P}^{\text{delay}}_{g,n} \quad \forall g \in \mathcal{G}^{\text{NR}}, n, t \tag{GEN-1b}
\]

\[
p_{g,n,t} \leq M \cdot u_{g,n,t} \quad \forall g \in \mathcal{G}^{\text{NR}}, n, t \tag{GEN-1c}
\]

where \(M = \max(1.1 \cdot \max(\mathbf{D}),\; 10^4)\) is a big-M constant.

!!! warning "Zero-capacity generators"
    When a generator has zero rated power, zero investment potential, and no delayed retirement at a bus, the model constrains \(p_{g,n,t} \leq 0\) for all \(t\). This prevents free generation from unconstrained variables that would have zero cost in the objective (see Critical Fix #12 in the project history).

**Configuration parameters:**

- `generators[g].rated_power[n]` -- installed capacity (MW)
- `generators[g].availability[t,n]` -- time-varying availability profile
- `generators[g].invest_max[n]` -- maximum investment (MW); development mode only

#### GEN-2: Minimum Generation (UC mode only)

\[
p_{g,n,t} \geq \underline{P}_{g,n} \cdot \bar{P}_{g,n} \cdot u_{g,n,t} \quad \forall g, n, t \tag{GEN-2}
\]

where \(\underline{P}_{g,n}\) is the minimum stable generation fraction (`min_power[n]`). This ensures that when a generator is committed (\(u=1\)), it produces at least its minimum output.

#### GEN-3: Ramp Rate Constraints

\[
p_{g,n,t} - p_{g,n,t-1} \leq \left( \bar{P}^{\text{eff}}_{g,n} + \hat{P}^{\text{inv}}_{g,n} \right) \cdot R^{\text{up}}_{g,n} \quad \forall g, n, t \geq 2 \tag{GEN-3a}
\]

\[
p_{g,n,t-1} - p_{g,n,t} \leq \left( \bar{P}^{\text{eff}}_{g,n} + \hat{P}^{\text{inv}}_{g,n} \right) \cdot R^{\text{down}}_{g,n} \quad \forall g, n, t \geq 2 \tag{GEN-3b}
\]

where \(R^{\text{up}}_{g,n}\) and \(R^{\text{down}}_{g,n}\) are per-unit ramp rates (`ramp_up[n]`, `ramp_down[n]`).

**Configuration parameters:**

- `generators[g].ramp_up[n]` -- ramp up limit as fraction of capacity per hour
- `generators[g].ramp_down[n]` -- ramp down limit as fraction of capacity per hour

#### GEN-4: Startup Detection (UC mode only)

\[
v_{g,n,t} \geq u_{g,n,t} - u_{g,n,t-1} \quad \forall g, n, t \tag{GEN-4}
\]

For \(t=1\), the previous status \(u_{g,n,0}\) is taken from `generator_initial_status`. The startup variable \(v_{g,n,t}\) captures the transition from off to on.

#### GEN-5: Minimum Up/Down Time (UC mode only)

**Minimum up time:** once a generator starts, it must remain on for at least \(T^{\text{up}}_g\) hours:

\[
u_{g,n,t} \geq u_{g,n,\tau} - u_{g,n,\tau-1} \quad \forall g, n, t, \; \tau \in [\max(1, t - T^{\text{up}}_g + 1),\, t] \tag{GEN-5a}
\]

**Minimum down time:** once a generator shuts down, it must remain off for at least \(T^{\text{down}}_g\) hours:

\[
1 - u_{g,n,t} \geq u_{g,n,\tau-1} - u_{g,n,\tau} \quad \forall g, n, t, \; \tau \in [\max(1, t - T^{\text{down}}_g + 1),\, t] \tag{GEN-5b}
\]

**Configuration parameters:**

- `generators[g].min_up_time[n]` -- minimum on-time (hours)
- `generators[g].min_down_time[n]` -- minimum off-time (hours)

---


### Battery Constraints

Implemented in `add_battery_constraints!()`.

#### BAT-1: Charge/Discharge Power Limits

\[
p^{\text{ch}}_{b,n,t} \leq \bar{P}^{\text{ch}}_{b,n} + \hat{P}^{\text{inv,P}}_{b,n} \quad \forall b, n, t \tag{BAT-1a}
\]

\[
p^{\text{dis}}_{b,n,t} \leq \bar{P}^{\text{dis}}_{b,n} + \hat{P}^{\text{inv,P}}_{b,n} \quad \forall b, n, t \tag{BAT-1b}
\]

where \(\bar{P}^{\text{ch}}_{b,n}\) and \(\bar{P}^{\text{dis}}_{b,n}\) are the maximum charge and discharge power ratings respectively.

#### BAT-2: SOC Dynamics

\[
E_{b,n,t+1} = E_{b,n,t} \cdot (1 - \rho_b) + \eta^{\text{ch}}_b \, p^{\text{ch}}_{b,n,t} - \frac{p^{\text{dis}}_{b,n,t}}{\eta^{\text{dis}}_b} - \text{spill}_{b,n,t} \quad \forall b, n, t \tag{BAT-2}
\]

where:

- \(\rho_b\) is the self-discharge rate per hour (`self_discharge[n]`)
- \(\eta^{\text{ch}}_b\) is the charging efficiency (`charge_efficiency[n]`)
- \(\eta^{\text{dis}}_b\) is the discharging efficiency (`discharge_efficiency[n]`)
- \(\text{spill}_{b,n,t}\) is an optional spillage term for batteries that allow energy release without grid injection

#### BAT-3: Initial SOC

\[
E_{b,n,1} = E^{0}_{b,n} \quad \forall b, n \tag{BAT-3}
\]

where \(E^{0}_{b,n} = \text{soc\_initial}_{b,n} \times \bar{E}_{b,n}\) is the initial energy stored, given as a fraction of total capacity.

#### BAT-4: SOC Bounds (Soft Upper)

\[
E_{b,n,t+1} \geq \underline{E}_b \cdot \bar{E}^{\text{eff}}_{b,n} \quad \forall b, n, t \tag{BAT-4a}
\]

\[
E_{b,n,t+1} \leq \overline{E}_b \cdot \bar{E}^{\text{eff}}_{b,n} + \sigma_{b,n,t} \quad \forall b, n, t \tag{BAT-4b}
\]

where \(\underline{E}_b\) and \(\overline{E}_b\) are the minimum and maximum SOC fractions (`soc_min[n]`, `soc_max[n]`), and \(\bar{E}^{\text{eff}}_{b,n}\) is the effective capacity (including investment). The slack variable \(\sigma_{b,n,t}\) allows soft violation of the upper bound, penalized heavily in the objective (\(C^{\text{SOC}}\)).

**Configuration parameters:**

- `batteries[b].soc_min[n]` -- minimum SOC as fraction of capacity
- `batteries[b].soc_max[n]` -- maximum SOC as fraction of capacity
- `soc_violation_penalty` -- penalty coefficient for SOC upper bound violation

#### BAT-5: End-of-Horizon Cyclic Constraint

\[
E^{0}_{b,n} \cdot (1 - \epsilon) \leq E_{b,n,T+1} \leq E^{0}_{b,n} \cdot (1 + \epsilon) \quad \forall b, n \tag{BAT-5}
\]

where \(\epsilon\) is the `soc_end_tolerance`. This prevents batteries from arbitrarily depleting stored energy over the optimization window.

!!! note "Why cyclic SOC matters"
    Without end-of-horizon constraints, batteries would discharge fully at the end of each window, acting as infinite energy sources across sequential windows. The tolerance \(\epsilon\) provides flexibility while maintaining energy conservation.

#### BAT-6: Charge/Discharge Mutual Exclusivity

\[
p^{\text{ch}}_{b,n,t} \leq M_{\text{bat}} \cdot \delta^{\text{ch}}_{b,n,t} \quad \forall b, n, t \tag{BAT-6a}
\]

\[
p^{\text{dis}}_{b,n,t} \leq M_{\text{bat}} \cdot (1 - \delta^{\text{ch}}_{b,n,t}) \quad \forall b, n, t \tag{BAT-6b}
\]

where \(M_{\text{bat}} = \max(10^6, 2 \cdot \max(\bar{P}^{\text{ch}}, \bar{P}^{\text{dis}}))\). This prevents simultaneous charging and discharging.

#### BAT-7: Minimum Cycling Requirement

\[
\sum_t p^{\text{ch}}_{b,n,t} \geq \frac{\gamma^{\text{cycle}} \cdot \bar{E}_{b,n} \cdot T_{\text{days}}}{T^{\text{cycle}}_{\text{period}}} \quad \forall b, n \tag{BAT-7}
\]

where \(\gamma^{\text{cycle}}\) is the `min_cycling_ratio` and \(T^{\text{cycle}}_{\text{period}}\) is `min_cycling_period_days`. Ensures batteries are actively utilized.

#### BAT-8: Spillage Power Limit

\[
\text{spill}_{b,n,t} \leq \bar{P}^{\text{dis}}_{b,n} + \hat{P}^{\text{inv,P}}_{b,n} \quad \forall b, n, t \tag{BAT-8}
\]

Only applies to batteries with `spillage = true`.

---


### Reserve Constraints

Implemented in `add_reserve_constraints!()`.

#### RES-1: Static Reserve Requirement

\[
\sum_{g \in \mathcal{G}^{\text{res}}} \left( \bar{P}_{g,n} \cdot a_{g,n,t} - p_{g,n,t} \right) + \lambda^{\text{sta}}_{n,t} \geq R^{\text{sta}}_n \quad \forall n, t \tag{RES-1}
\]

The available reserve is the difference between available capacity and dispatched output for reservable generators (\(\mathcal{G}^{\text{res}}\)). The shortage slack \(\lambda^{\text{sta}}_{n,t}\) is penalized in the objective.

\(R^{\text{sta}}_n\) is either specified per bus (`reserve_static_requirement[n]`) or defaults to `reserve_static_default_ratio` \(\times\) bus demand.

#### RES-2: Dynamic Reserve

\[
r^{\text{dyn}}_{n,t} \leq \sum_{g \in \mathcal{G}^{\text{res}}} \alpha^{\text{dyn}} \cdot \bar{P}_{g,n} \quad \forall n, t \tag{RES-2a}
\]

\[
r^{\text{dyn}}_{n,t} + \lambda^{\text{dyn}}_{n,t} \geq R^{\text{dyn}}_n \quad \forall n, t \tag{RES-2b}
\]

where \(\alpha^{\text{dyn}}\) is the `dynamic_reserve_contribution` factor (fraction of rated power available for dynamic response).

---


### Power Balance Constraints


#### PB-1: Single-Bus Power Balance

Implemented in `add_demand_constraints!()`. For single-bus systems:

\[
\sum_g p_{g,n,t} + \sum_b p^{\text{dis}}_{b,n,t} + p^{\text{v2g}}_{n,t} + \text{LS}_{n,t} + P^{\text{roof}}_{n,t} + \sum_s \text{FC}_{s,n,t} \tag{PB-1}
\]

\[
= D_{n,t} + p^{\text{elz}}_{n,t} + \sum_b p^{\text{ch}}_{b,n,t} + r^{\text{sta}}_{n,t} + r^{\text{dyn}}_{n,t} + p^{\text{ev,ch}}_{n,t} + \text{RC}_{n,t}
\]

where \(D_{n,t} = D^{\text{node}}_{t,n_i} \cdot \alpha_n\) is the bus demand derived from the parent node's demand scaled by the bus demand fraction, and \(P^{\text{roof}}_{n,t}\) is behind-the-meter rooftop solar generation.

!!! note "Curtailment is not in the power balance"
    Curtailment (\(\text{CU}_{n,t}\)) does not appear in the power balance equation. It represents energy that was never dispatched (the difference between available and used renewable generation), not energy that enters and then leaves the system.

**Load shedding threshold** (B9): If configured, load shedding is capped:

\[
\text{LS}_{n,t} \leq \theta^{\text{LS}} \cdot D_{n,t} \quad \text{when } \theta^{\text{LS}} < 1 \tag{PB-1b}
\]

#### DC-1: Multi-Bus Power Balance (KCL)

Implemented in `add_dc_constraints!()` in `transmission_dc.jl`. For multi-bus networks, the power balance includes transmission flows:

\[
\underbrace{\sum_g p_{g,n,t} + \sum_b p^{\text{dis}}_{b,n,t} + p^{\text{v2g}}_{n,t} + \text{LS}_{n,t} + P^{\text{conv}}_{n,t} + \sum_s \text{FC}_{s,n,t}}_{\text{supply}} \tag{DC-1}
\]

\[
- \underbrace{D_{n,t} - p^{\text{elz}}_{n,t} - \sum_b p^{\text{ch}}_{b,n,t} - r^{\text{sta}}_{n,t} - r^{\text{dyn}}_{n,t} - p^{\text{ev,ch}}_{n,t}}_{\text{demand}} = \sum_{\ell} K_{n,\ell} \, f_{\ell,t}
\]

where \(K_{n,\ell}\) is the bus-line incidence matrix element (\(+1\) if line \(\ell\) leaves bus \(n\), \(-1\) if it enters), and \(P^{\text{conv}}_{n,t}\) aggregates AC/DC and frequency converter injections at the bus. Transmission losses are applied to incoming flows: the effective coefficient for incoming flow is \(K_{n,\ell} \cdot (1 - \rho_\ell)\) where \(\rho_\ell\) is the per-line loss factor.

The constraint reference is stored for dual price extraction.

#### DC-2: Kirchhoff's Voltage Law (KVL)

For each independent cycle \(c\):

\[
\sum_\ell C_{\ell,c} \cdot x_\ell \cdot f_{\ell,t} = 0 \quad \forall c, t \tag{DC-2}
\]

where \(C_{\ell,c}\) is the cycle-line incidence matrix and \(x_\ell\) is the line reactance. This enforces loop consistency of voltage drops.

#### DC-3: Slack Bus Reference

\[
\theta_{\text{slack},t} = 0 \quad \forall t \tag{DC-3}
\]

#### DC-4: Voltage Angle Limits

\[
|\theta_{i,t} - \theta_{j,t}| \leq \bar{\theta} \quad \forall (i,j) \in \mathcal{L}, t \tag{DC-4}
\]

where \(\bar{\theta}\) is `max_angle_diff_rad`. Only enforced when `enable_angle_limits` is true.

#### DC-5: Line Capacity Limits

Implemented in `add_line_capacity_constraints!()`:

\[
-\left(\bar{F}_\ell + \hat{F}^{\text{inv}}_\ell\right) \leq f_{\ell,t} \leq \bar{F}_\ell + \hat{F}^{\text{inv}}_\ell \quad \forall \ell, t \tag{DC-5}
\]

where \(\bar{F}_\ell\) is the thermal capacity and \(\hat{F}^{\text{inv}}_\ell\) is the transmission investment variable (development mode only).

#### DC-6: Converter Capacity Limits

Implemented in `add_converter_constraints!()`:

\[
p^{\text{rect}}_{k,t} + p^{\text{inv}}_{k,t} \leq \bar{S}_k \quad \forall k, t \tag{DC-6a}
\]

\[
p^{A \to B}_{f,t} + p^{B \to A}_{f,t} \leq \bar{S}_f \quad \forall f, t \tag{DC-6b}
\]

where \(\bar{S}\) is the rated apparent power (MVA) of the converter.

---


### Curtailment Constraints

Implemented in `add_curtailment_constraints!()`.

#### CUR-1: Curtailment Definition

\[
\text{CU}_{n,t} = \sum_{g \in \mathcal{G}^{\text{RE}}} \left(\bar{P}^{\text{eff}}_{g,n} + \hat{P}^{\text{inv}}_{g,n}\right) a_{g,n,t} - \sum_{g \in \mathcal{G}^{\text{RE}}} p_{g,n,t} \quad \forall n, t \tag{CUR-1}
\]

Curtailment is defined as the difference between available renewable capacity and dispatched renewable generation at each bus and hour.

#### CUR-2: Curtailment Upper Bound

\[
\text{CU}_{n,t} \leq \sum_{g \in \mathcal{G}^{\text{RE}}} \left(\bar{P}^{\text{eff}}_{g,n} + \hat{P}^{\text{inv}}_{g,n}\right) a_{g,n,t} \quad \forall n, t \tag{CUR-2}
\]

Curtailment cannot exceed available renewable generation.

#### CUR-3: Maximum Curtailment Ratio

\[
\sum_{n,t} \text{CU}_{n,t} \leq \rho^{\text{CU}} \cdot \sum_{g \in \mathcal{G}^{\text{RE}}, n, t} p_{g,n,t} \quad \tag{CUR-3}
\]

where \(\rho^{\text{CU}}\) is the `max_curtailment_ratio` (default: 0.05, i.e., 5%). This is a **hard constraint** (not a penalty): total curtailment across all buses and hours cannot exceed 5% of total renewable generation.

This constraint serves two purposes:
1. **Storage investment signal**: By limiting curtailment, the optimizer is forced to invest in battery storage or transmission to absorb excess renewable generation.
2. **Policy compliance**: Many grid codes limit the fraction of renewable energy that may be wasted.

When `max_curtailment_ratio = 1.0`, the constraint is inactive (all renewable generation may be curtailed).

---


### Renewable Penetration Constraint

Implemented in `add_renewable_constraint!()`.

#### RE-1: System-Wide Renewable Penetration Target

\[
\Delta t \cdot \left( \sum_{g \in \mathcal{G}^{\text{RE}}, n, t} p_{g,n,t} + \sum_{b,n,t} p^{\text{dis}}_{b,n,t} \right) + \Delta t \cdot \sum_{n,t} \phi_{n,t} \geq \tau^{\text{RE}} \cdot E^{\text{total}}_{\text{demand}} \tag{RE-1}
\]

where:

- \(\Delta t\) is the temporal resolution (hours)
- \(\tau^{\text{RE}}\) is `re_penetration_target`
- \(E^{\text{total}}_{\text{demand}} = \sum_{t,n} D_{t,n} \cdot \Delta t\) is total energy demand

Battery discharge is counted toward renewable delivery because storage shifts renewable energy in time. The slack variable \(\phi_{n,t}\) is penalized by \(C^{\text{FRE}}\) in the objective.

!!! note "RE penetration is measured against demand, not generation"
    This is a policy constraint. The target \(\tau^{\text{RE}}\) represents the fraction of total demand that should be served by renewable sources (including battery-mediated delivery).

---


### CO2 Emission Constraints

Implemented in `add_co2_emissions_definition!()` and `add_co2_constraint!()`.

#### CO2-1: Emissions Definition

\[
\text{CO2}_{n,t} = \sum_g e_{\text{fuel}(g)} \cdot p_{g,n,t} \quad \forall n, t \tag{CO2-1}
\]

where \(e_f\) is the emission factor (tonnes CO2/MWh) for fuel type \(f\), looked up from `fuel_co2`.

#### CO2-2: CO2 Budget Constraint

\[
\sum_{g,n,t} e_{\text{fuel}(g)} \cdot p_{g,n,t} \leq \frac{T \cdot \Delta t}{8760} \cdot B^{\text{CO2}} + \xi^{\text{CO2}} \tag{CO2-2}
\]

The annual CO2 budget \(B^{\text{CO2}}\) (`co2_budget`, tonnes/year) is scaled by the window fraction \(T \cdot \Delta t / 8760\). The violation slack \(\xi^{\text{CO2}}\) is penalized by `co2_budget_violation_penalty` in the objective.

---


### Inertia Constraint

Implemented in `add_inertia_constraints!()`.

#### INE-1: System Inertia Requirement

\[
\sum_{g,n} p_{g,n,t} \cdot h_{g,n} + \sum_{b,n} \left(p^{\text{ch}}_{b,n,t} + p^{\text{dis}}_{b,n,t}\right) \cdot h^{\text{bat}}_{b,n} + H_t \geq H^{\min}_t \quad \forall t \tag{INE-1}
\]

where:

- \(h_{g,n}\) is the inertia constant of generator \(g\) at bus \(n\) (`inertia[n]`)
- \(h^{\text{bat}}_{b,n}\) is the synthetic inertia constant of battery \(b\) (`inertia[n]`)
- \(H^{\min}_t\) is the minimum inertia requirement (from `inertia_limit` or `inertia_limit_hourly[t]`)
- \(H_t\) is the inertia shortfall slack, penalized by `loss_of_inertia_penalty`

Inertia is contributed by synchronous generators proportionally to their output and by storage systems proportionally to their power throughput.

---


### EV Fleet Constraints

Implemented in `add_ev_constraints!()`.

#### EV-1: SOC Dynamics

\[
E^{\text{ev}}_{n,t+1} = E^{\text{ev}}_{n,t} + \eta^{\text{ev,ch}} \cdot p^{\text{ev,ch}}_{n,t} - \frac{p^{\text{v2g}}_{n,t}}{\eta^{\text{ev,dis}}} \quad \forall n, t \tag{EV-1}
\]

#### EV-2: Charging Demand Satisfaction

\[
p^{\text{ev,ch}}_{n,t} + \ell^{\text{ev}}_{n,t} \geq D^{\text{ev}}_{n,t} \quad \forall n, t \tag{EV-2}
\]

where \(D^{\text{ev}}_{n,t}\) is the driving energy consumption profile. The slack \(\ell^{\text{ev}}_{n,t}\) allows unmet EV charging demand (penalized in objective).

#### EV-3: Charging Power Limit

\[
p^{\text{ev,ch}}_{n,t} \leq \max\left( \frac{\bar{P}^{\text{ev,ch}}_{\text{kW}} \cdot N^{\text{ev}}_n}{1000},\; 2 \cdot D^{\text{ev}}_{n,t} \right) \quad \forall n, t \tag{EV-3}
\]

The maximum charging power is the aggregate fleet charging capacity (kW to MW conversion), with a floor of twice the driving consumption for feasibility.

#### EV-4: V2G Discharge Limit

\[
p^{\text{v2g}}_{n,t} \leq \frac{\bar{P}^{\text{ev,dis}}_{\text{kW}} \cdot N^{\text{ev}}_n}{1000} \cdot \alpha^{\text{ev}}_{n,t} \quad \forall n, t \tag{EV-4}
\]

where \(\alpha^{\text{ev}}_{n,t}\) is the EV availability profile (fraction of fleet connected to grid).

#### EV-5: SOC Bounds

\[
\underline{E}^{\text{ev}} \cdot \bar{E}^{\text{ev}}_n \leq E^{\text{ev}}_{n,t+1} \leq \overline{E}^{\text{ev}} \cdot \bar{E}^{\text{ev}}_n \quad \forall n, t \tag{EV-5}
\]

where \(\bar{E}^{\text{ev}}_n = E^{\text{ev}}_{\text{kWh}} \cdot N^{\text{ev}}_n / 1000\) is the total fleet battery capacity in MWh.

#### EV-6: Charge/V2G Mutual Exclusivity

\[
p^{\text{ev,ch}}_{n,t} \leq M_{\text{ev}} \cdot \delta^{\text{ev}}_{n,t} \quad \forall n, t \tag{EV-6a}
\]

\[
p^{\text{v2g}}_{n,t} \leq M_{\text{ev}} \cdot (1 - \delta^{\text{ev}}_{n,t}) \quad \forall n, t \tag{EV-6b}
\]

where \(M_{\text{ev}} = P^{\text{max,ch}} + P^{\text{max,v2g}}\).

---


### Sectoral Demand Constraints

Implemented in `add_sectoral_demand_constraints!()`.

#### SEC-1: LOL Decomposition

\[
\sum_{s \in \mathcal{S}} \text{LOL}_{s,n,t} = \text{LS}_{n,t} \quad \forall n, t \tag{SEC-1}
\]

Total load shedding is decomposed into per-sector contributions.

#### SEC-2: Per-Sector LOL Cap

\[
\text{LOL}_{s,n,t} \leq D^{s}_{n,t} \cdot \alpha_n \quad \forall s, n, t \tag{SEC-2}
\]

No sector can shed more load than its demand.

#### SEC-3: Flexible Demand Curtailment Cap

\[
\text{FC}_{s,n,t} \leq D^{s}_{n,t} \cdot \alpha_n \quad \forall s, n, t \tag{SEC-3}
\]

Flexible demand curtailment cannot exceed the sector's demand. This bound is essential because \(\text{FC}\) enters the objective with a negative (benefit) coefficient; without it, the LP would be unbounded.

!!! note "Criticality ordering"
    Higher-criticality sectors receive a higher load shedding penalty in the objective (\(C^{\text{VOLL}} \cdot \kappa_s\)). The optimizer naturally sheds lower-criticality sectors first. No explicit ordering constraint is needed.

---


### Demand Shifting Constraints

Implemented within `add_sectoral_demand_constraints!()`.

#### SHIFT-1: Shift-Out Capacity

\[
\sum_{t' \neq t} \Delta_{s,n,t,t'} \leq (1 - \kappa_s) \cdot D^{s}_{n,t} \cdot \alpha_n \quad \forall s, n, t \tag{SHIFT-1}
\]

The total demand shifted away from hour \(t\) is limited by the flexibility ratio \((1 - \kappa_s)\) of the sector's demand. Only flexible sectors (\(\kappa_s < 1\)) have shift variables, and shifts are restricted to a temporal delay tolerance window: \(|t - t'| \leq \tau^{\text{delay}}_s\).

**Configuration parameters:**

- `sectoral_criticality[s]` -- criticality weight (0 = fully flexible, 1 = fully critical)
- `sectoral_delay_tolerance[s]` -- maximum shift window in hours (default 24)
- `demand_shift_cost_rate` -- cost per MW per hour of shift distance

---


### N-1 Security Constraints

Implemented in `add_n1_security_constraints!()`.

#### N1-1: Generation N-1

\[
\sum_{\substack{g,n \\ (g,n) \neq (g^*,n^*)}} p_{g,n,t} \geq \sum_n D_{n,t} \quad \forall t \tag{N1-1}
\]

where \((g^*, n^*)\) is the largest generator in the system. The remaining generation must be sufficient to cover total demand if the largest unit trips.

#### N1-2: Transmission N-1

\[
-\bar{F}_\ell \cdot \omega \leq f_{\ell,t} \leq \bar{F}_\ell \cdot \omega \quad \forall \ell \in \mathcal{L}^{\text{crit}}, t \tag{N1-2}
\]

where \(\omega\) is the `n1_transmission_reserve_factor` (typically 0.7--0.9). Critical lines have their usable capacity reduced to reserve headroom for post-contingency flow redistribution.

**Configuration parameters:**

- `n1_security_enabled` -- master switch for N-1 constraints
- `n1_generation_enabled` -- enable generation N-1
- `n1_transmission_enabled` -- enable transmission N-1
- `n1_transmission_reserve_factor` -- fraction of line capacity usable under N-1

---


### Node Investment Limits (Development Mode)

Implemented in `add_node_investment_limits!()`.

#### INV-1: Per-Node Investment Cap

\[
\sum_g \hat{P}^{\text{inv}}_{g,n} + \sum_b \hat{P}^{\text{inv,P}}_{b,n} \leq \bar{I}_n \quad \forall n \tag{INV-1}
\]

where \(\bar{I}_n\) is `max_node_investment[n]` (MW).

---


### Maximum Annual System Cost

Implemented in `add_max_annual_system_cost!()`.

#### COST-1: Operating Cost Ceiling

\[
\sum_{g,n,t} \left( c^{\text{fuel}}_{g,n} + c^{\text{fixed}}_{g,n} + c^{\text{maint}}_{g,n} \right) p_{g,n,t} \leq C^{\max}_{\text{annual}} \tag{COST-1}
\]

---


## 5. Degradation and Age-Based Capacity


Effective capacity decreases with unit age:

\[
\bar{P}^{\text{eff}}_{g,n} = \bar{P}_{g,n} \cdot (1 - d_g)^{\text{age}_{g,n}} \tag{DEG-1}
\]

where \(d_g\) is the annual degradation rate (`degradation_rate[n]`) and:

| Unit Type | Age Formula |
|-----------|-------------|
| Existing unit | \(\text{age} = \text{initial\_age} + (\text{year\_idx} - 1)\) |
| New investment | \(\text{age} = \text{year\_idx} - \text{investment\_year}\) |

If the remaining lifetime (\(\text{lifetime} - \text{age}\)) is zero or negative, the effective capacity is set to zero and the unit is effectively retired.

The same degradation logic applies to batteries, where capacity, maximum charge power, and maximum discharge power are all degraded by the same factor:

\[
\bar{E}^{\text{eff}}_{b,n} = \bar{E}_{b,n} \cdot (1 - d_b)^{\text{age}_{b,n}}, \quad \bar{P}^{\text{ch,eff}}_{b,n} = \bar{P}^{\text{ch}}_{b,n} \cdot (1 - d_b)^{\text{age}_{b,n}} \tag{DEG-2}
\]

---


## 6. Dual Variables and Electricity Prices


In LP mode (economic dispatch or relaxed unit commitment), the shadow prices of the power balance constraints yield the locational marginal price (LMP):

\[
\pi_{n,t} = \text{dual}\left(\text{balance\_constraint}_{n,t}\right) \tag{DUAL-1}
\]

The constraint references are stored in `vars.balance_constraints[(n,t)]` during construction of either `add_demand_constraints!()` (single bus) or `add_dc_constraints!()` (multi-bus). Prices are extracted in `extract_solution()` using JuMP's `dual()` function.

!!! warning "Duals are unavailable in MIP mode"
    When the model contains binary variables (unit commitment mode), dual prices are not directly available from the solver. The model must be fixed and re-solved as an LP to obtain meaningful price signals.

---


## 7. Solution Extraction


After solving, `extract_solution()` computes the following aggregate metrics:

| Metric | Formula |
|--------|---------|
| Total generation | \(\sum_{g,n,t} p_{g,n,t}\) |
| Total curtailment | \(\sum_{n,t} \text{CU}_{n,t}\) |
| Total load shedding | \(\sum_{n,t} \text{LS}_{n,t}\) |
| RE penetration | \(\sum_{g \in \mathcal{G}^{\text{RE}}} p_g / \sum_g p_g\) |
| Total CO2 | \(\sum_g e_{\text{fuel}(g)} \sum_{n,t} p_{g,n,t}\) |

---


## 8. Parameter Summary


Penalty coefficients and their configuration paths:

| Parameter | Config Field | Default | Description |
|-----------|-------------|---------|-------------|
| \(C^{\text{VOLL}}\) | `loss_of_load_penalty` | -- | Value of lost load ($/MWh) |
| \(C^{\text{CU}}\) | `curtailment_penalty` | -- | Curtailment cost ($/MWh) |
| \(C^{\text{r,sta}}\) | `loss_of_reserve_static` | -- | Static reserve shortage ($/MW) |
| \(C^{\text{r,dyn}}\) | `loss_of_reserve_dynamic` | -- | Dynamic reserve shortage ($/MW) |
| \(C^{\text{ine}}\) | `loss_of_inertia_penalty` | -- | Inertia shortfall penalty |
| \(C^{\text{ev}}\) | `ev_config.loss_penalty` | -- | EV demand unmet penalty |
| \(C^{\text{FRE}}\) | `fre_penetration_penalty` | 100 | RE target shortfall ($/MWh) |
| \(C^{\text{SOC}}\) | `soc_violation_penalty` | -- | SOC limit violation penalty |
| \(C^{\text{TM}}\) | `transfer_margin_penalty` | 10% of VOLL | Transmission violation |
| \(C^{\text{CO2,bud}}\) | `co2_budget_violation_penalty` | 500 | CO2 budget excess ($/tonne) |
| \(C^{\text{RC}}\) | `rooftop_curtailment_penalty` | 5.0 | Rooftop solar curtailment |
| \(C^{\text{delay}}\) | `delay_retirement_penalty_per_mw` | -- | Delayed retirement ($/MW) |
| \(\gamma^{\text{shift}}\) | `demand_shift_cost_rate` | 0.1 | Shift cost per MW-hour distance |

!!! note "Penalty tuning"
    Penalty coefficients establish a merit order for constraint relaxation. They should be set so that \(C^{\text{VOLL}} \gg C^{\text{CU}} > C^{\text{FRE}}\) to ensure that load shedding is the last resort, curtailment is preferred to shedding, and RE targets create appropriate investment signals without dominating the objective.

---


## 9. Implementation Reference

| Julia Function | Section | File |
|----------------|---------|------|
| `create_power_system()` | Top-level model builder | `power_system.jl` |
| `build_variables!()` | All variable creation | `power_system.jl` |
| `build_objective!()` | Objective function | `power_system.jl` |
| `add_generator_constraints!()` | GEN-1 through GEN-5 | `power_system.jl` |
| `add_battery_constraints!()` | BAT-1 through BAT-8 | `power_system.jl` |
| `add_reserve_constraints!()` | RES-1, RES-2 | `power_system.jl` |
| `add_demand_constraints!()` | PB-1 (single bus) | `power_system.jl` |
| `add_dc_constraints!()` | DC-1 through DC-4 | `transmission_dc.jl` |
| `add_line_capacity_constraints!()` | DC-5 | `transmission_dc.jl` |
| `add_converter_constraints!()` | DC-6 | `transmission_dc.jl` |
| `add_curtailment_constraints!()` | CUR-1, CUR-2, CUR-3 | `power_system.jl` |
| `add_renewable_constraint!()` | RE-1 | `power_system.jl` |
| `add_co2_emissions_definition!()` | CO2-1 | `power_system.jl` |
| `add_co2_constraint!()` | CO2-2 | `power_system.jl` |
| `add_inertia_constraints!()` | INE-1 | `power_system.jl` |
| `add_ev_constraints!()` | EV-1 through EV-6 | `power_system.jl` |
| `add_sectoral_demand_constraints!()` | SEC-1 through SEC-3, SHIFT-1 | `power_system.jl` |
| `add_n1_security_constraints!()` | N1-1, N1-2 | `power_system.jl` |
| `add_node_investment_limits!()` | INV-1 | `power_system.jl` |
| `add_max_annual_system_cost!()` | COST-1 | `power_system.jl` |
| `extract_solution()` | Solution extraction and metrics | `power_system.jl` |

---


## 10. Rolling Horizon Implementation


The full year (8760 hours) is solved as a sequence of overlapping windows rather than a single monolithic problem, reducing memory requirements and solve time by orders of magnitude.

### 10.1 Window Structure

The year is divided into windows of `rolling_horizon_hours` (default: 48) with `overlap_hours` (default: 6) of overlap:

```
Window 1: hours  1 — 48  → keep hours  1 — 42
Window 2: hours 43 — 90  → keep hours 43 — 84
Window 3: hours 85 — 132 → keep hours 85 — 126
...
```

The overlap ensures smooth transitions for battery SOC and generator status between windows.

### 10.2 Initial Conditions

Each window (except the first) receives initial conditions from the previous window:

| State Variable | Initial Condition |
|----------------|-------------------|
| Battery SOC | SOC at last kept hour of previous window |
| Generator status (UC) | On/off status at last kept hour |
| Demand shift state | Accumulated shifted energy |

### 10.3 Window Boundary Handling

At the end of each window, the cyclic SOC constraint (BAT-5) ensures batteries return close to their initial SOC for that window. This prevents batteries from "gaming" the horizon boundary by depleting at the window edge.

### 10.4 Result Stitching

Results from all windows are concatenated to form the full-year dispatch:

\[
\mathbf{p}^{year} = [\mathbf{p}^{w_1}_{1:42}, \mathbf{p}^{w_2}_{1:42}, \ldots, \mathbf{p}^{w_N}_{1:r}]
\]

where the last window may have fewer than 42 kept hours (remainder of the year).

### 10.5 Sub-Hourly Resolution

When `temporal_resolution < 1.0` (e.g., 0.5 hours), each hour contains `1/resolution` time steps. A 48-hour window with 30-minute resolution has 96 time steps. All constraints remain the same but operate on the finer time grid.

---

## References

The unit commitment formulation and rolling horizon dispatch follow the classical treatment in Wood et al. [**[31]**](../reference/bibliography.md#ref31) (Chs. 2, 4). The importance of chronological operational detail in planning models is studied by Poncelet et al. [**[26]**](../reference/bibliography.md#ref26) and Palmintier and Webster [**[25]**](../reference/bibliography.md#ref25). Battery storage modeling with cyclic SOC constraints and degradation draws on Xu et al. [**[32]**](../reference/bibliography.md#ref32). Load shedding with value of lost load (VOLL) prioritization follows the review by Schröder and Kuckshinrichs [**[33]**](../reference/bibliography.md#ref33). N-1 security reserve constraints follow Capitanescu et al. [**[34]**](../reference/bibliography.md#ref34). The optimization is formulated via JuMP [**[20]**](../reference/bibliography.md#ref20) and solved with HiGHS [**[21]**](../reference/bibliography.md#ref21) (LP/MIP) or Ipopt [**[4]**](../reference/bibliography.md#ref4) (ACOPF).

See the [full bibliography](../reference/bibliography.md) for complete citation details.
