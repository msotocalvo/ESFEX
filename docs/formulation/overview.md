# Notation & Conventions
## Model Architecture

ESFEX is a multi-temporal, multi-nodal power system optimization framework. It co-optimizes capacity expansion, operational dispatch, and sector coupling through a hierarchical decomposition into two levels:

- A long-term **Master Problem** that determines investment and retirement trajectories across all planning years simultaneously.
- Short-term **Operational Dispatch** subproblems that validate those decisions at hourly resolution using a rolling time horizon.

Cumulative capacity expressions connect the two levels, translating investment variables into available generation, storage, and transmission capacity for each year. Additional modules handle network physics (DC power flow), fuel supply logistics (primary energy), hydrogen production (electrolyzer), and electric vehicle fleet integration.

The formulation is implemented in Julia via the JuMP [**[20]**](../reference/bibliography.md#ref20) algebraic modeling language and solved as LP or MIP depending on the operating mode.


## Model Components

The six optimization models that compose the ESFEX framework are summarized below.

| Model | Julia File | Purpose | Type |
|-------|-----------|---------|------|
| [Operational Dispatch](operational-dispatch.md) | `power_system.jl` | Hourly generation dispatch and unit commitment | LP / MIP |
| [Capacity Expansion](capacity-expansion.md) | `master_problem.jl` | Multi-year investment and retirement planning | LP |
| [DC Power Flow](dc-power-flow.md) | `transmission_dc.jl` | Network-constrained power flow | LP (embedded) |
| [AC OPF](ac-power-flow.md) | `transmission_acopf.jl` | Network-constrained AC optimal power flow | NLP / SOCP (embedded) |
| [AC Verification](dc-power-flow.md#ac-power-flow-verification) | `transmission_ac.jl` | Post-DC voltage and reactive power check | NR (post-processing) |
| [Primary Energy](primary-energy.md) | `primary_energy.jl` | Multi-fuel supply chain optimization | LP (embedded) |
| [Electrolyzer](electrolyzer.md) | `electrolyzer.jl` | Power-to-hydrogen conversion | LP (embedded) |
| [Reservoir Hydropower](reservoir-hydro.md) | `power_system.jl` | Water-energy budget: seasonal storage, cascade, head, min flow | LP (embedded) |
| [MGA/SPORES](capacity-expansion.md#15-mgaspores-near-optimal-exploration) | `mga.jl` | Near-optimal alternative generation | LP |

The Master Problem determines investment decisions across all years and passes them to the Operational Dispatch, which solves each year in detail through rolling horizon windows. DC Power Flow is embedded in both models when transmission constraints are active. When `power_flow_mode` is set to an ACOPF formulation, the operational dispatch uses full AC power flow equations instead of the DC approximation — modeling voltage magnitudes, reactive power, and apparent power limits (see [AC OPF](ac-power-flow.md)). After DC-OPF dispatch, an optional AC power flow verification step uses Newton-Raphson to validate voltage profiles, reactive power flows, and actual transmission losses — identifying violations not visible under the DC approximation. The Primary Energy and Electrolyzer modules couple fuel supply with electrical dispatch: generator fuel consumption must be consistent with fuel availability, and hydrogen demand must be satisfied by electrolyzer output.


## Objective Function

The system-level objective minimizes the net present value (NPV) of total costs across the planning horizon:

\[
\min \; Z = \sum_{y=1}^{Y} \frac{1}{(1+r)^{y-1}} \left[ C^{\text{inv}}_y + C^{\text{op}}_y + C^{\text{pen}}_y \right]
\]

where:

- \(C^{\text{inv}}_y\) — investment costs: generators, batteries, transmission, primary energy infrastructure, and electrolyzers (see [Capacity Expansion — Objective](capacity-expansion.md#5-objective-function))
- \(C^{\text{op}}_y\) — operational costs: fuel, O&M, startup, battery cycling, converter operation, electrolyzer operation, and demand shifting (see [Operational Dispatch — Objective](operational-dispatch.md#3-objective-function))
- \(C^{\text{pen}}_y\) — penalty costs: load shedding, curtailment, reserve shortfalls, inertia shortfalls, RE target violations, CO\(_2\) budget violations, SOC violations, and transfer margin violations

Investment costs are determined by the Master Problem. Operational and penalty costs are evaluated through representative day subproblems at the planning level, then refined through full rolling-horizon dispatch at the operational level.


## Notation


### Sets and Indices

| Symbol | Index | Description |
|--------|-------|-------------|
| \(\mathcal{G}\) | \(g\) | Generators |
| \(\mathcal{G}_{RE} \subseteq \mathcal{G}\) | \(g\) | Renewable generators |
| \(\mathcal{G}_{NR} \subseteq \mathcal{G}\) | \(g\) | Non-renewable (conventional) generators |
| \(\mathcal{B}\) | \(b\) | Batteries (energy storage systems) |
| \(\mathcal{N}\) | \(n\) | Buses (electrical nodes) |
| \(\mathcal{T}\) | \(t\) | Time steps (hours within an operational window) |
| \(\mathcal{Y}\) | \(y\) | Planning years |
| \(\mathcal{L}\) | \(\ell\) | Transmission lines (physical) |
| \(\mathcal{C}\) | \(c\) | Independent network cycles |
| \(\mathcal{S}\) | \(s\) | Demand sectors |
| \(\mathcal{F}\) | \(f\) | Fuels |
| \(\mathcal{D}_y\) | \(d\) | Representative days for year \(y\) |
| \(\mathcal{K}\) | \(k\) | AC/DC converters |
| \(\Omega\) | \(\omega\) | Stochastic scenarios |


### Decision Variables

| Symbol | Units | Model | Description |
|--------|-------|-------|-------------|
| \(P_{g,n,t}\) | MW | Dispatch | Generator active power output |
| \(u_{g,n,t}\) | \(\{0,1\}\) or 1 | Dispatch | Generator commitment status (binary in UC, fixed to 1 in ED) |
| \(v_{g,n,t}\) | \([0,1]\) | Dispatch | Startup indicator (UC mode only) |
| \(P^{ch}_{b,n,t}\) | MW | Dispatch | Battery charging power |
| \(P^{dis}_{b,n,t}\) | MW | Dispatch | Battery discharging power |
| \(E_{b,n,t}\) | MWh | Dispatch | Battery state of charge |
| \(C_{n,t}\) | MWh | Dispatch | Renewable energy curtailment |
| \(L_{n,t}\) | MW | Dispatch | Load shedding (unserved demand) |
| \(f_{\ell,t}\) | MW | DC Flow | Transmission line power flow |
| \(\theta_{n,t}\) | rad | DC Flow | Bus voltage angle |
| \(I^{gen}_{y,g,n}\) | MW | Expansion | Generator capacity investment |
| \(I^{bat,P}_{y,b,n}\) | MW | Expansion | Battery power capacity investment |
| \(I^{bat,E}_{y,b,n}\) | MWh | Expansion | Battery energy capacity investment |
| \(I^{tr}_{y,i,j}\) | MW | Expansion | Transmission line capacity investment |
| \(P^{elz}_{n,t}\) | MW | Electrolyzer | Electrolyzer power consumption |
| \(H_{n,t}\) | kg/h | Electrolyzer | Hydrogen production rate |
| \(S_{f,n,p}\) | units/period | Primary Energy | Fuel supply rate |
| \(T_{f,i,j,p}\) | units/period | Primary Energy | Fuel transport flow |
| \(V_{f,n,p}\) | units | Primary Energy | Fuel storage inventory level |
| \(P^{ev,ch}_{n,t}\) | MW | EV | EV fleet charging power |
| \(P^{v2g}_{n,t}\) | MW | EV | Vehicle-to-grid discharge power |
| \(E^{ev}_{n,t}\) | MWh | EV | EV fleet aggregate state of charge |


### Parameters

| Symbol | Units | Description |
|--------|-------|-------------|
| \(\bar{P}_{g,n}\) | MW | Rated power of generator \(g\) at node \(n\) |
| \(c^{fuel}_{g,n}\) | $/MWh | Fuel cost |
| \(c^{inv}_{g,n}\) | $/MW | Investment cost per unit capacity |
| \(\bar{I}_{g,n}\) | MW | Maximum allowable investment capacity |
| \(\alpha_{g,t,n}\) | \([0,1]\) | Availability factor (time-varying, for renewable generators) |
| \(\delta_{g,n}\) | \([0,1]\) | Annual capacity degradation rate |
| \(\tau_{g,n}\) | years | Technical lifetime |
| \(\eta^{ch}_{b,n}\), \(\eta^{dis}_{b,n}\) | \([0,1]\) | Battery charging and discharging round-trip efficiencies |
| \(D_{n,t}\) | MW | Electrical demand at node \(n\), time step \(t\) |
| \(X_\ell\) | p.u. | Transmission line reactance |
| \(\bar{F}_\ell\) | MW | Transmission line thermal capacity |
| \(r\) | \([0,1]\) | Annual discount rate |
| \(VOLL\) | $/MWh | Value of lost load |
| \(\rho^{target}\) | \([0,1]\) | Target renewable energy penetration ratio |
| \(M_{res}\) | — | Reserve margin multiplier (default 1.15) |
| \(\gamma\) | — | Annual demand growth rate |


### Typographic Conventions

- **Subscripts:** \(g\) generator, \(b\) battery, \(n\) node/bus, \(t\) time step, \(y\) year, \(s\) sector, \(\ell\) line, \(f\) fuel, \(d\) representative day, \(k\) converter index
- **Superscripts:** \(ch\) charge, \(dis\) discharge, \(inv\) investment, \(gen\) generation, \(tr\) transmission, \(elz\) electrolyzer, \(ev\) electric vehicle
- \(\bar{x}\) denotes an upper bound or rated value; \(\underline{x}\) denotes a lower bound or minimum
- \(\mathbb{1}[\cdot]\) is the indicator function, evaluated at model construction time (not a decision variable)
- All monetary values are in US dollars ($) unless otherwise noted
- Time resolution \(\Delta t\) defaults to 1 hour; configurable via `temporal_resolution_hours`


## Solver Configuration

All models are formulated through JuMP [**[20]**](../reference/bibliography.md#ref20) and solved with HiGHS [**[21]**](../reference/bibliography.md#ref21) as the default open-source solver. HiGHS handles LP, MIP, and QP problems. The commercial solvers Gurobi and CPLEX can be selected through the `solver` section of the YAML configuration file for larger-scale instances.

The Master Problem is a pure LP — continuous variables with age-based retirement evaluated at construction time. The Operational Dispatch is LP in economic dispatch mode and MIP in unit commitment mode.


## Simulation Modes

| Mode | Generator Status | Investment Variables | Use Case |
|------|-----------------|---------------------|----------|
| `development` | Fixed = 1 (LP) | Present | Capacity planning and technology screening |
| `economic_dispatch` | Fixed = 1 (LP) | Absent | Operational cost evaluation with a fixed fleet |
| `unit_commitment` | Binary \(\{0,1\}\) (MIP) | Absent | Detailed operational analysis with startup/shutdown |

In `development` mode, generator commitment is relaxed to continuous, keeping the full problem as an LP with investment variables. In `unit_commitment` mode, binary commitment variables introduce minimum up/down time constraints, startup costs, and minimum stable generation levels, yielding a MIP.


## Modeling Assumptions

1. **DC power flow approximation with AC verification** [**[1]**](../reference/bibliography.md#ref1). Linearization of the AC power flow equations under the assumption of small voltage angle deviations and reactance-dominant lines. Losses are represented via a piecewise-linear approximation of the quadratic \(I^2R\) relationship. An optional post-optimization AC power flow step (Newton-Raphson) validates the DC solution by computing actual voltage magnitudes, reactive power flows, and true losses, flagging any voltage or thermal violations that the DC approximation may miss.

2. **Perfect foresight within each window.** Demand and renewable availability are known within each rolling horizon window. Cross-year uncertainty is handled through the stochastic extension (see [Stochastic Programming](stochastic-programming.md)).

3. **Hourly resolution.** The default time step is 1 hour, configurable via `temporal_resolution_hours`. The Master Problem uses representative days (typically 24-hour blocks) selected by peak demand ranking or TSAM clustering [**[22]**](../reference/bibliography.md#ref22), [**[23]**](../reference/bibliography.md#ref23).

4. **Representative days.** The Master Problem approximates annual operational costs from a small set of selected days (typically 3–10) rather than full 8760-hour dispatch. The number of days controls the trade-off between approximation quality and problem size.

5. **LP retirement.** Units retire deterministically when their age exceeds their configured lifetime. No binary retirement decisions are introduced, preserving the LP structure of the Master Problem.

6. **Soft constraints.** Policy and reliability constraints — demand satisfaction, reserves, RE targets, CO\(_2\) budgets — are enforced through penalty-based slack variables. This guarantees feasibility and allows the modeler to identify binding constraints through non-zero slack values.

7. **Exponential degradation.** Generator and battery capacity degrades as \(\bar{P} \cdot (1 - \delta)^{\text{age}}\), applied uniformly to rated power, charge/discharge power, and energy capacity.


## Data Flow

```
Configuration (YAML)
    |
    v
Master Problem (all years, representative days)
    |-- Investment decisions: I_gen, I_bat, I_tr
    |-- RE penetration trajectory: rho_y
    |-- Cumulative capacity expressions
    |
    v
Operational Dispatch (per year, rolling horizon windows)
    |-- Uses cumulative capacities from Master Problem
    |-- Solves configurable overlapping time windows
    |-- Returns: generation profiles, dual prices, emissions
    |
    v
Results & Reporting (HDF5, CSV)
```

The Master Problem passes investment decisions and cumulative capacities to the Operational Dispatch. Each year is solved independently using a rolling time horizon with configurable window size and overlap. Availability profiles are preloaded once and cached to avoid redundant I/O across windows.


## References

The ESFEX formulation builds on the state of the art in generation expansion planning [**[24]**](../reference/bibliography.md#ref24), integrating operational flexibility constraints [**[25]**](../reference/bibliography.md#ref25), [**[26]**](../reference/bibliography.md#ref26) within a two-stage decomposition. The optimization is formulated using the JuMP modeling language [**[20]**](../reference/bibliography.md#ref20) and solved with HiGHS [**[21]**](../reference/bibliography.md#ref21) (LP/MIP) or Ipopt [**[4]**](../reference/bibliography.md#ref4) (NLP). DC power flow follows the analysis by Stott et al. [**[1]**](../reference/bibliography.md#ref1); ACOPF relaxations follow Jabr [**[2]**](../reference/bibliography.md#ref2), Coffrin et al. [**[3]**](../reference/bibliography.md#ref3), and Low [**[27]**](../reference/bibliography.md#ref27). Stochastic programming follows Birge and Louveaux [**[9]**](../reference/bibliography.md#ref9). MGA/SPORES follows Lombardi et al. [**[7]**](../reference/bibliography.md#ref7). Sobol sensitivity analysis follows Sobol [**[11]**](../reference/bibliography.md#ref11) and Saltelli et al. [**[12]**](../reference/bibliography.md#ref12). For a comprehensive review of flexibility in high-RE systems, see Denholm and Hand [**[28]**](../reference/bibliography.md#ref28), IEA [**[29]**](../reference/bibliography.md#ref29), and Kondziella and Bruckner [**[30]**](../reference/bibliography.md#ref30).

See the [full bibliography](../reference/bibliography.md) for the complete list of references cited throughout the documentation.
