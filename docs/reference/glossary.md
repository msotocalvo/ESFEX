# Glossary

## A

**Adjacency Matrix**
:   A symmetric $N \times N$ matrix where entry $(i,j)$ represents the transmission capacity (MW) between nodes $i$ and $j$. Zero entries indicate no direct connection. Stored flattened in the YAML configuration as `nodes_connections`.

**Availability Profile**
:   A time series of capacity factors (0 to 1) for a generator, representing the fraction of rated power available at each hour. Derived from weather data for renewables (solar irradiance, wind speed).

---

## B

**Boundary Conditions**
:   State variables passed between consecutive rolling horizon windows: battery SOC, generator on/off status, and EV fleet SOC. Ensures continuity across optimization windows.

**Bus**
:   An electrical connection point within a node, defined by voltage level, frequency, and current type (AC/DC). Multiple buses can exist within a single node (e.g., HV and LV buses connected by a transformer).

---

## C

**Capacity Expansion Planning (CEP)**
:   Long-term optimization determining what generation, storage, and transmission capacity to build, when to build it, and when to retire existing assets. In ESFEX, this is the Master Problem.

**Capacity Factor (CF)**
:   The ratio of actual energy output to maximum possible output: $CF = E_{actual} / (P_{rated} \times T)$. Values range from 0 to 1.

**CostCurveBlock**
:   A single segment in a piecewise-linear cost curve, defined by a power output fraction (0 to 1 of $P_{max}$) and a marginal cost ($/MWh). Multiple blocks form a stepwise non-decreasing offer curve. Used internally after `normalize_cost_curve()` converts any curve type (flat, linear, stepwise, exponential) to a uniform block representation.

**Curtailment**
:   Deliberate reduction of renewable energy output below available capacity. ESFEX can limit curtailment to a configurable fraction (`max_curtailment_ratio`) of total renewable generation.

---

## D

**DC Power Flow**
:   A linearized approximation of AC power flow using voltage angles and line reactances. Assumes lossless lines, flat voltage profiles, and small angle differences.

**Demand Sector**
:   A categorization of electrical demand by end-use (residential, commercial, industrial) with attributes like criticality, flexibility, and shedding priority.

**Development Zone**
:   A geographic polygon representing an area designated for renewable energy or other technology development, with associated maximum capacity and interconnection costs.

**Discount Rate**
:   The rate used to calculate Net Present Value (NPV) of future costs and revenues. Configured via the `discount_rate` parameter.

---

## E

**Economic Dispatch (ED)**
:   Operational optimization that determines the least-cost generation schedule to meet demand, subject to technical constraints. All variables are continuous (no binary on/off decisions).

**Endpoint Reference (EndpointRef)**
:   A data structure identifying which element a line endpoint connects to, consisting of `element_type` (node, generator, battery, etc.) and `element_id`.

**EVPI (Expected Value of Perfect Information)**
:   The difference between the wait-and-see solution (perfect foresight) and the here-and-now solution (decisions before uncertainty resolves). Quantifies the maximum value of perfect forecasts.

---

## H

**HDF5 (Hierarchical Data Format 5)**
:   The primary output format for ESFEX simulation results. Supports hierarchical groups, large multi-dimensional arrays, and metadata attributes.

**HiGHS**
:   The default open-source LP/MIP solver used by ESFEX.

---

## I

**Inertia**
:   Kinetic energy stored in rotating generators that resists frequency changes. Measured in MW*s. Renewables (except some wind turbines) provide zero inertia, requiring constraints to maintain system inertia above a threshold.

**Investment Resolution**
:   The time granularity of the Master Problem. Default: 8760 hours (annual).

---

## J

**JuMP**
:   Julia for Mathematical Programming. The optimization modeling framework used by ESFEX's Julia backend.

**juliacall**
:   Python package (via PythonCall.jl) enabling Julia calls from Python. Bridges the Python orchestrator with the Julia optimization backend.

---

## K

**KCL (Kirchhoff's Current Law)**
:   At every bus, the sum of power injections equals the sum of power withdrawals. Forms the network power balance constraint in the DC power flow formulation.

**KVL (Kirchhoff's Voltage Law)**
:   Around every independent cycle in the network, the sum of voltage drops equals zero. Enforced through cycle matrix constraints in DC power flow.

---

## L

**LCOE (Levelized Cost of Energy)**
:   The average cost per unit of energy produced over a generator's lifetime, including capital, fuel, and O&M costs: $LCOE = \frac{\text{Total Lifetime Cost}}{\text{Total Lifetime Generation}}$

**Loss of Load (LOL)**
:   Unserved energy demand. Penalized via `LOSS_DEMAND_TRHESHOLD` ($/MWh) to ensure it only occurs when physically unavoidable. The penalty value is user-configurable.

---

## M

**Magnetic Snapping**
:   GUI feature where equipment (generators, batteries, fuel entries) automatically attaches to the nearest network node when placed on the map.

**Master Problem**
:   The capacity expansion optimization that determines investment and retirement decisions across all planning years. Minimizes NPV of total system costs subject to RE targets, budget constraints, and operational feasibility.

**MCDA (Multi-Criteria Decision Analysis)**
:   Structured evaluation of alternatives against multiple criteria. Used in the GUI's analysis wizards for renewable energy site selection.

**MGA (Modeling to Generate Alternatives)**
:   Explores the near-optimal solution space by generating $K$ structurally diverse alternatives within a user-defined cost slack (e.g., 5% above optimum). ESFEX uses the classical Hop-Skip-Jump (HSJ) formulation [**[8]**](bibliography.md#ref8): each alternative is found by maximising a single diversity objective that penalises investment variables seen in previous alternatives. Configuration: `master_problem.mga.method = "mga"`, `num_alternatives`, `slack_fraction`. **Distinct from [SPORES](#sporesspatially-explicit-practically-optimal-results-exploration)**, which replaces the single diversity objective with a *menu* of distinct objectives (one per alternative).

---

## N

**N-1 Security**
:   Contingency analysis ensuring the power system remains stable after the loss of any single element (line or generator). Implemented as reserve constraints in the optimization.

**Node**
:   A geographic location in the power system network. Each node has demand, connected generators, batteries, and fuel infrastructure. Nodes are connected by transmission lines.

**NPV (Net Present Value)**
:   The sum of discounted future costs: $NPV = \sum_{y} \frac{C_y}{(1+r)^y}$ where $C_y$ is the cost in year $y$ and $r$ is the discount rate.

**NTC (Net Transfer Capacity)**
:   Maximum power (MW) reliably transferable between two areas through interconnecting transmission lines. Replaced in ESFEX by a DC-OPF formulation with physical power flow and losses.

---

## O

**Operational Dispatch**
:   The short-term optimization that determines hour-by-hour generation and storage schedules. Runs within the rolling horizon framework using investment decisions from the Master Problem.

**Overlap Hours**
:   Hours shared between consecutive rolling horizon windows for smooth transitions. Configured via `overlap_hours`.

---

## P

**Penalty Coefficient**
:   Cost assigned to constraint violations (load shedding, reserve deficit, etc.) to make violations expensive but not infinite, maintaining solver feasibility.

**Polyline Trace**
:   The GUI method for drawing transmission lines and fuel routes as multi-segment paths with intermediate waypoints.

**Primary Energy**
:   The fuel supply chain modeling subsystem, including fuel imports, storage, transport, and non-electric demand.

**PTDF (Power Transfer Distribution Factor)**
:   A matrix of sensitivity coefficients describing how power flow on each transmission line changes in response to a unit injection at each node. Used in the DC power flow formulation to convert nodal injections to line flows: $f_l = \sum_n PTDF_{l,n} \cdot P_n$. Computed from the network admittance matrix and topology.

**PWL (Piecewise Linear)**
:   Approximation of nonlinear functions using linear segments. In ESFEX, used for transmission loss modeling ($I^2R$ losses) and generator/battery cost curves (output blocks with non-decreasing marginal costs).

---

## R

**Representative Day**
:   A selected peak-demand day used in the Master Problem to validate operational feasibility of investment decisions.

**RE Penetration**
:   The fraction of total electricity generation from renewable sources: $RE = \frac{\sum RE\_generation}{\sum total\_generation}$

**Rolling Horizon**
:   Decomposition of the annual dispatch problem into overlapping time windows. Window size (`rolling_horizon_hours`) and overlap (`overlap_hours`) are user-configurable. Boundary conditions (battery SOC, generator status) pass between windows for continuity.

---

## S

**S-Curve (Logistic Growth)**
:   The adoption model for EV fleet growth: $N(t) = N_0 \times \frac{M}{1 + (M-1) \cdot e^{-k(t-t_0)}}$ where $M$ is the maximum adoption multiplier and $k$ is the growth rate.

**SOC (State of Charge)**
:   The current energy level of a battery as a fraction or absolute value of its total capacity. Bounded by minimum (depth of discharge) and maximum limits. Subject to a cyclic constraint in ESFEX: final SOC at the end of each optimization window must match the initial SOC, preventing the battery from acting as a net energy source or sink.

**Sobol Indices**
:   Global sensitivity analysis metrics. First-order ($S_1$) measures individual parameter influence. Total-order ($S_T$) includes interactions. Second-order ($S_2$) measures pairwise interactions.

**SPORES (Spatially-explicit Practically Optimal Results Exploration)**
:   A method for generating diverse near-optimal alternatives in energy system models, introduced by Lombardi et al. (2020) [**[7]**](bibliography.md#ref7) for the Calliope framework. SPORES differs from classical [MGA](#mgamodeling-to-generate-alternatives) by replacing the single diversity objective with a *menu* of objectives — each alternative is the LP solution to a different objective under the same cost-slack constraint $Z \leq (1+\varepsilon) C^*$. ESFEX ships four canonical SPORES objectives: $\min$ total build (smallest portfolio), max technology equity (min-max over per-tech totals), max regional equity (min-max over per-node totals — the *spatially explicit* objective at the heart of the SPORES name), and max evolutionary distance (L1 distance from the cost-optimal plan). Configuration: `master_problem.mga.method = "spores"`, `objectives = [...]`. The number of alternatives equals `len(objectives)`; `num_alternatives` is ignored.

**Sysimage**
:   Precompiled Julia system image (`.so`/`.dll`) bundling ESFEX Julia code and dependencies. Eliminates JIT compilation overhead, reducing startup from minutes to seconds. Created with `PackageCompiler.jl`.

---

## T

**Transfer Margin**
:   Remaining capacity on a transmission line after accounting for power flows.

---

## U

**Unit Commitment (UC)**
:   Operational optimization with binary on/off decisions for generators, including minimum up/down time and start-up cost constraints. Computationally harder than ED (MIP).

---

## V

**V2G (Vehicle-to-Grid)**
:   The capability of electric vehicles to discharge power back to the grid, providing ancillary services and flexibility. Modeled in ESFEX with a participation rate (fraction of connected EVs willing to discharge), round-trip efficiency, and SOC constraints that respect driver mobility needs.

**VALLCOE (Value-Adjusted Levelized Cost of Energy)**
:   LCOE adjusted for the time-varying value of electricity produced. A generator with high CF during peak hours has lower VALLCOE than one generating mostly off-peak.

**Virtual Generator**
:   A synthetic generator configuration created dynamically from technology investment decisions for use in operational dispatch. When the master problem decides to invest in a technology (e.g., 87 MW of Solar PV), a virtual generator with that rated power is created and added to the dispatch model alongside existing physical generators. Virtual generators appear after original units in the HDF5 output and are named with an "Investment" prefix (e.g., "Investment Solar PV").

**Visual Style**
:   GUI dataclass controlling element appearance on the map: color, size, shape, opacity, and line width.

**VSS (Value of Stochastic Solution)**
:   Benefit of stochastic programming over deterministic. Computed as the difference between the expected cost of the deterministic solution across all scenarios and the stochastic solution cost. High VSS indicates uncertainty significantly affects planning decisions.

---

## W

**Waypoint**
:   An intermediate geographic point on a transmission line or fuel route polyline trace. Allows lines to follow realistic geographic paths rather than straight-line connections.

**Window**
:   A single time period in the rolling horizon decomposition. Each window covers `rolling_horizon_hours` and overlaps with adjacent windows by `overlap_hours`. Both parameters are user-configurable.
