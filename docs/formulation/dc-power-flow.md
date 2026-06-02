# DC Power Flow

The DC power flow model implements network-constrained power flow using Kirchhoff's laws. It is implemented in `transmission_dc.jl` and based on the cycle-flow formulation from Horsch et al. (2018).

## Overview

The DC power flow approximation linearizes the AC power flow equations under the assumptions that:

- Voltage magnitudes are close to 1 p.u.
- Voltage angle differences across lines are small
- Line resistance is negligible compared to reactance

This yields a linear model where power flows are determined by voltage angle differences and line reactances.

## Bus vs. Node

ESFEX distinguishes between **buses** (electrical abstraction) and **nodes** (geographic locations):

| Concept | Description | Example |
|---------|-------------|---------|
| **Node** | Geographic location with coordinates | City, substation site |
| **Bus** | Electrical point in the network | HV bus, LV bus at same substation |

Each bus has a `parent_node` and carries a `demand_fraction` of its parent node's total demand. Multiple buses can map to the same node (e.g., different voltage levels at the same substation).

## Graph Structures

### Incidence Matrix

The bus-line incidence matrix \(\mathbf{K} \in \mathbb{R}^{N \times L}\) encodes the network topology:

\[
K_{n,l} = \begin{cases}
+1 & \text{if line } l \text{ leaves bus } n \\
-1 & \text{if line } l \text{ enters bus } n \\
0 & \text{otherwise}
\end{cases}
\tag{DC-K}
\]

Built by `build_incidence_matrix()`. Supports parallel lines between the same bus pair (each is a separate column).

### Cycle Matrix

The cycle-line matrix \(\mathbf{C} \in \mathbb{R}^{L \times |\mathcal{C}|}\) encodes the independent cycles found via spanning tree analysis:

\[
C_{l,c} = \begin{cases}
+1 & \text{if line } l \text{ is in cycle } c \text{ (forward direction)} \\
-1 & \text{if line } l \text{ is in cycle } c \text{ (reverse direction)} \\
0 & \text{otherwise}
\end{cases}
\tag{DC-C}
\]

Built by `find_cycles()` using the **Graphs.jl** library:

1. Construct a spanning tree of the network graph
2. Each non-tree edge creates exactly one independent cycle
3. For each non-tree edge, find the path through the tree between its endpoints
4. The cycle = tree path + non-tree edge

## Constraints

### DC-1: KCL (Kirchhoff's Current Law)

Power balance at each bus \(n\) and time \(t\), with transmission losses on incoming flows:

\[
\underbrace{\sum_{g} P_{g,n,t} + \sum_{b} P^{dis}_{b,n,t} + L_{n,t}}_{\text{injection}} - \underbrace{D_{n,t} \cdot \phi_n + \sum_{b} P^{ch}_{b,n,t} + R^{st}_{n,t} + R^{dyn}_{n,t}}_{\text{withdrawal}} = \sum_{l} K_{n,l} \cdot f^{loss}_{l,t}
\tag{DC-1}
\]

where the loss-adjusted flow is:

\[
f^{loss}_{l,t} = \begin{cases}
f_{l,t} & \text{if } K_{n,l} < 0 \text{ (outgoing)} \\
f_{l,t} \cdot (1 - \lambda_l) & \text{if } K_{n,l} > 0 \text{ (incoming)}
\end{cases}
\]

Here \(\lambda_l\) is the per-line loss factor (from `resistance_pu` for transmission lines, `losses_fraction` for transformers).

!!! note "Injection Terms"
    The KCL injection also includes EV V2G power, flexible demand curtailed, and converter flows (AC/DC, frequency converters) when applicable.

Implemented in `add_dc_constraints!()`.

### DC-2: KVL (Kirchhoff's Voltage Law)

For each independent cycle \(c\), the sum of voltage drops around the cycle must be zero:

\[
\sum_{l \in \mathcal{C}_c} C_{l,c} \cdot X_l \cdot f_{l,t} = 0 \quad \forall c, t
\tag{DC-2}
\]

where \(X_l\) is the line reactance. For parallel lines, the effective reactance is `reactance_pu / num_circuits`.

This constraint ensures that power flows are consistent with the physical network impedances, not just the KCL balance.

### DC-3: Line Capacity

Each line has a thermal capacity limit:

\[
-\bar{F}_l \leq f_{l,t} \leq \bar{F}_l \quad \forall l, t
\tag{DC-3}
\]

where \(\bar{F}_l\) is the total transfer capacity, which may include investment:

\[
\bar{F}_l = F^{base}_l + I^{tr}_{l}
\]

Implemented in `add_line_capacity_constraints!()`.

### DC-4: Voltage Angle Limits

Voltage angle differences across each line are bounded:

\[
|\theta_{n_1,t} - \theta_{n_2,t}| \leq \theta^{max} \quad \forall (n_1, n_2) \in \mathcal{L}, t
\tag{DC-4}
\]

where \(\theta^{max}\) is the maximum angle difference (default: 30 degrees = 0.524 rad).

### DC-5: Slack Bus Reference

One bus is designated as the slack (reference) bus with angle fixed to zero:

\[
\theta_{n^{ref},t} = 0 \quad \forall t
\tag{DC-5}
\]

By default, bus 1 is the slack bus.

## Converter Constraints

### AC/DC Converters

AC/DC converters (VSC or LCC type) allow power transfer between AC and DC networks with directional efficiency:

\[
P^{conv}_{n,t} \leq \bar{P}^{conv}_n + I^{conv}_n
\tag{CONV-1}
\]

\[
P^{ac}_{n,t} = \begin{cases}
P^{conv}_{n,t} \cdot \eta^{rect} & \text{rectifying (AC→DC)} \\
P^{conv}_{n,t} / \eta^{inv} & \text{inverting (DC→AC)}
\end{cases}
\tag{CONV-2}
\]

### Frequency Converters

Similar to AC/DC converters but connecting networks of different frequencies (e.g., 50 Hz to 60 Hz):

\[
P^{freq}_{n,t} \leq \bar{P}^{freq}_n + I^{freq}_n
\tag{FREQ-1}
\]

Implemented in `add_converter_constraints!()`.

## Transformer Modeling

Transformers are modeled as additional branches appended to the line set:

- **Reactance**: Series reactance from `reactance_pu` field
- **Capacity**: Thermal limit from `rated_power_mva`
- **Losses**: Per-transformer `losses_fraction`
- **Tap ratio**: Not modeled in DC approximation (linear model)

Self-loop transformers (both ends at the same bus) are skipped with a warning.

## Transmission Losses

ESFEX supports three transmission loss models, selected via `dc_power_flow.loss_model`:

| Mode | Key | Description | Formulation |
|------|-----|-------------|-------------|
| Lossless | `"none"` | No transmission losses | \(P^{loss}_{l,t} = 0\) |
| Linear (legacy) | `"linear"` | Constant loss factor on incoming flows | \(P^{loss}_{l,t} = \lambda_l \cdot |f_{l,t}|\) |
| Piecewise linear | `"pwl"` | PWL approximation of quadratic \(I^2R\) losses (default) | See below |

### Physical Model

Real power losses on a transmission line are governed by the quadratic relationship:

\[
P^{loss}_l(f) = g_l \cdot f_l^2 \tag{LOSS-PHYS}
\]

where \(g_l\) is the line conductance derived from the series impedance:

\[
g_l = \frac{R_l}{R_l^2 + X_l^2}
\]

with \(R_l\) the resistance and \(X_l\) the reactance (both in per-unit). Direct inclusion of the quadratic term would make the problem a QP. Instead, we use a piecewise linear (PWL) approximation that preserves LP compatibility.

### PWL Approximation

The quadratic loss curve is approximated by \(N\) linear segments (configured via `pwl_loss_segments`). The flow range \([0, \bar{F}_l]\) is divided into \(N\) equal segments with breakpoints:

\[
f_k = k \cdot \Delta f, \qquad \Delta f = \frac{\bar{F}_l}{N}, \qquad k = 0, 1, \ldots, N
\]

The slope of the \(k\)-th segment (\(k = 1, \ldots, N\)) is derived from the secant of the quadratic between breakpoints \(f_{k-1}\) and \(f_k\):

\[
m_k = g_l \cdot (2k - 1) \cdot \Delta f \tag{LOSS-SLOPE}
\]

Since \(m_1 < m_2 < \cdots < m_N\) (monotonically increasing slopes), the PWL approximation forms a **convex** function. This convexity property is critical: for a cost-minimizing LP, the solver will naturally fill lower-slope segments first, so no binary variables are needed to enforce segment ordering.

### Flow Decomposition

Because losses depend on the magnitude of flow (not its direction), each line flow is decomposed into positive and negative components:

\[
f_{l,t} = f^{+}_{l,t} - f^{-}_{l,t}, \qquad f^{+}_{l,t}, f^{-}_{l,t} \geq 0 \tag{LOSS-1}
\]

Each direction is further decomposed into \(N\) segment variables:

\[
f^{+}_{l,t} = \sum_{k=1}^{N} \delta^{+}_{l,k,t}, \qquad f^{-}_{l,t} = \sum_{k=1}^{N} \delta^{-}_{l,k,t} \tag{LOSS-2}
\]

with segment width bounds:

\[
0 \leq \delta^{+}_{l,k,t} \leq \Delta f, \qquad 0 \leq \delta^{-}_{l,k,t} \leq \Delta f \tag{LOSS-3}
\]

### PWL Loss Computation

The total loss on each line is the sum of segment contributions from both directions:

\[
P^{loss}_{l,t} = \sum_{k=1}^{N} m_k \cdot \left( \delta^{+}_{l,k,t} + \delta^{-}_{l,k,t} \right) \tag{LOSS-4}
\]

### Half-Loss Split KCL

Losses are allocated equally between the two endpoint buses of each line. The KCL equation (DC-1) becomes:

\[
\text{net\_inj}_{n,t} = \sum_{l} \left( K_{n,l} \cdot f_{l,t} - \frac{1}{2} \cdot |K_{n,l}| \cdot P^{loss}_{l,t} \right) \tag{LOSS-5}
\]

where \(K_{n,l}\) is the incidence matrix entry and \(|K_{n,l}|\) ensures both endpoint buses absorb half the loss regardless of flow direction. This formulation is symmetric and avoids the need to track flow direction for loss allocation.

### Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `loss_model` | `"pwl"` | Loss model selection: `"none"`, `"linear"`, or `"pwl"` |
| `pwl_loss_segments` | 3 | Number of PWL segments for operational dispatch |
| `pwl_loss_segments_master` | 2 | Number of PWL segments for master problem (fewer for performance) |

Increasing the number of segments improves accuracy but adds variables and constraints. Three segments typically capture over 95% of the quadratic loss curve.

## Power Balance: Single Bus vs. Multi-Bus

| Network Size | Model | Function |
|-------------|-------|----------|
| 1 bus | Simple power balance (PB-1) | `add_demand_constraints!()` |
| 2+ buses | DC power flow (DC-1 through DC-5) | `add_dc_constraints!()` |

The model automatically selects the appropriate formulation based on `network.num_buses`.

## Inter-System DC Power Flow

When multiple power systems are coupled via inter-system transmission links (defined in `MetaNetworkConfig.systems_links`), ESFEX implements a **bidirectional DC power flow model** with piecewise-linear loss approximation. This model treats inter-system links as HVDC-like connections with independent voltage references in each system.

### Architecture

Unlike intra-system transmission (which enforces Kirchhoff's Voltage Law across all network cycles), inter-system links:

- **Do not enforce KVL** across systems (independent voltage angle references)
- **Inject/withdraw power** at boundary nodes via the `external_injections` parameter
- **Model losses** using the same PWL approach as intra-system lines (or linear fallback)
- **Support investment** in both directions symmetrically

This is analogous to HVDC interconnectors between asynchronous AC systems.

### Variables

For each inter-system link \(l\) connecting system A (node \(n_A\)) to system B (node \(n_B\)):

| Variable | Bounds | Description |
|----------|--------|-------------|
| \(pf_l\) | Unbounded | Net power flow (positive = A→B, negative = B→A) |
| \(fp_l\) | \(\geq 0\) | Forward flow component (A→B) |
| \(fn_l\) | \(\geq 0\) | Reverse flow component (B→A) |
| \(dp_{l,k}\) | \([0, \Delta f]\) | Segment \(k\) flow (forward direction) |
| \(dn_{l,k}\) | \([0, \Delta f]\) | Segment \(k\) flow (reverse direction) |
| \(ploss_l\) | \(\geq 0\) | Total power loss on link \(l\) |

### Constraints

#### IS-1: Bidirectional Flow Decomposition

The net flow is decomposed into positive (forward) and negative (reverse) components:

\[
pf_l = fp_l - fn_l
\tag{IS-1}
\]

#### IS-2 & IS-3: Capacity Limits

Each link has thermal capacity limits in both directions, including investment:

\[
pf_l \leq \bar{F}_l^{base} + \sum_{y} I_{l,y}^{inter}
\tag{IS-2}
\]

\[
pf_l \geq -\left(\bar{F}_l^{base} + \sum_{y} I_{l,y}^{inter}\right)
\tag{IS-3}
\]

where \(I_{l,y}^{inter}\) is the inter-system link investment in year \(y\).

#### IS-4: PWL Loss Approximation

If the link has both `reactance_pu` and `resistance_pu` defined, losses are modeled using the same PWL approach as intra-system lines. The link conductance is:

\[
g_l = \frac{R_l}{R_l^2 + X_l^2}
\]

Each direction is decomposed into \(N\) PWL segments (configured via `inter_system_loss_segments`):

\[
fp_l = \sum_{k=1}^{N} dp_{l,k}, \quad fn_l = \sum_{k=1}^{N} dn_{l,k}
\]

with segment slopes:

\[
m_k = g_l \cdot (2k - 1) \cdot \Delta f, \quad \Delta f = \frac{\bar{F}_l}{N}
\]

The total loss is:

\[
ploss_l = \sum_{k=1}^{N} m_k \left( dp_{l,k} + dn_{l,k} \right)
\tag{IS-4}
\]

**Linear loss fallback**: If `resistance_pu` or `reactance_pu` is not provided (or if `inter_system_loss_segments = 0`), the model falls back to:

\[
ploss_l = \lambda_l \cdot |pf_l| = \lambda_l \cdot (fp_l + fn_l)
\]

where \(\lambda_l\) is the `loss_factor` from the config.

#### IS-5 & IS-6: KCL Injection with Half-Loss Split

The inter-system link injects/withdraws power at the boundary nodes with losses split equally:

**System A (FROM bus)**:

\[
\text{external\_inj}_{n_A} = +pf_l - 0.5 \cdot ploss_l
\tag{IS-5}
\]

**System B (TO bus)**:

\[
\text{external\_inj}_{n_B} = -pf_l - 0.5 \cdot ploss_l
\tag{IS-6}
\]

The negative sign on the TO bus ensures power leaves System A and enters System B (accounting for losses).

### Configuration

Inter-system transmission links are configured in `MetaNetworkConfig.systems_links`:

```yaml
meta_network:
  systems_links:
    - systems: ["SystemA", "SystemB"]
      connections: [[0, 1]]  # Node pairs
      existing_capacity_MW: [500.0]
      max_investment_MW: [500.0]
      investment_cost_per_MW: [1000000.0]
      loss_factor: [0.02]  # Linear loss if R/X not provided
      reactance_pu: [0.01]  # Series reactance (p.u.)
      resistance_pu: [0.001]  # Series resistance (p.u.)
      distance_km: [100.0]
      cost_per_mw_km: [10000.0]
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `reactance_pu` | list[float] | `[0.01]` | Per-link series reactance (p.u.) |
| `resistance_pu` | list[float] | `[0.001]` | Per-link series resistance (p.u.) |

Global setting:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `inter_system_loss_segments` | int | 2 | PWL segments for inter-system links (0 = linear fallback, max 5) |

### Implementation

- **Master Problem**: Inter-system variables/constraints are added in `master_problem.jl` via `_add_inter_system_constraints!()`
- **Operational Dispatch**: Each system receives `external_injections` kwarg in `add_day_operational_constraints!()` with link flows computed by the master problem
- **No KVL**: Inter-system links do NOT participate in the cycle matrix \(\mathbf{C}\) — each system has independent voltage angle references

### References

This formulation is similar to HVDC modeling in production cost models (e.g., PLEXOS, PowerSimulations.jl), where DC links provide controllable power transfer without enforcing AC voltage angle consistency.

---


## N-1 Security-Constrained DC Power Flow

When N-1 security is enabled (`n1_security.enabled: true`), the optimizer adds contingency constraints ensuring the system remains feasible after the loss of any single critical element.

### Critical Element Identification

Not all elements need to be tested as contingencies. ESFEX identifies critical elements to reduce computational burden:

**Transmission lines:** A line is critical if its base-case loading exceeds the `critical_threshold`:

\[
\frac{|f_{l,t}|}{\bar{F}_l} \geq \tau^{crit} \quad \text{for any } t
\tag{N1-CRIT}
\]

where \(\tau^{crit}\) is the `critical_threshold` (default: 0.50). Lines loaded below 50% in the base case are unlikely to cause issues when outaged.

**Generators:** The largest online generator (or the generator with the highest output) is tested. The reserve type determines the required generation reserve:

| Reserve Type | Reserve Requirement |
|-------------|---------------------|
| `largest_unit` | Reserve ≥ output of largest online generator |
| `percentage` | Reserve ≥ `reserve_percentage` × total demand |
| `fixed` | Reserve ≥ fixed MW value |

### Post-Contingency Constraints

#### N1-1: Line Outage

For each critical line \(l_c\), the remaining network must serve all demand without any remaining line exceeding the post-contingency loading limit:

\[
|f_{l,t}^{(l_c)}| \leq \bar{F}_l \cdot \rho^{post} \quad \forall l \neq l_c, \forall t
\tag{N1-1}
\]

where:
- \(f_{l,t}^{(l_c)}\) is the flow on line \(l\) after outage of line \(l_c\)
- \(\rho^{post}\) is the `transmission_reserve_factor` (default: 0.70)

The post-contingency flows are computed using Power Transfer Distribution Factors (PTDFs):

\[
f_{l,t}^{(l_c)} = f_{l,t} + \text{PTDF}_{l,l_c} \cdot f_{l_c,t}
\]

The PTDF matrix describes how the outaged line's flow redistributes across the remaining network.

#### N1-2: Generator Outage

The system must maintain sufficient spinning reserve to cover the loss of the largest generator:

\[
\sum_{g \neq g_c} \left(\bar{P}_{g,n} - p_{g,n,t}\right) \cdot u_{g,n,t} \geq P_{g_c,n,t} \quad \forall t
\tag{N1-2}
\]

This ensures that the headroom on committed generators can compensate for the lost unit.

### PTDF Matrix Construction

The PTDF (Power Transfer Distribution Factor) matrix is computed from the network admittance matrix:

\[
\text{PTDF} = \mathbf{B}_f \cdot \mathbf{B}^{-1}_{bus}
\]

where:
- \(\mathbf{B}_{bus}\) is the bus susceptance matrix (imaginary part of admittance)
- \(\mathbf{B}_f\) is the branch-bus susceptance matrix

For the DC approximation, susceptances are simply the inverse of reactances: \(b_l = 1/X_l\).

The PTDF matrix is precomputed once and reused for all contingency evaluations.

### LODF-Based Fast Contingency Analysis

Line Outage Distribution Factors (LODFs) enable O(1) per-contingency evaluation without rebuilding the B-matrix:

\[
\text{LODF}_{l,k} = \frac{\text{PTDF}_{l,\text{from}_k} - \text{PTDF}_{l,\text{to}_k}}{1 - (\text{PTDF}_{k,\text{from}_k} - \text{PTDF}_{k,\text{to}_k})}
\tag{LODF}
\]

Post-contingency flow on line \(l\) when line \(k\) trips:

\[
f_l^{(k)} = f_l + \text{LODF}_{l,k} \cdot f_k
\]

Both PTDF and LODF matrices are precomputed once and cached. The Python `ContingencyAnalyzer` uses LODF for `analyze_line_loss_fast()`, while the Julia SCOPF directly embeds LODF constraints into the optimization.

### Contingency Screening

Not all contingencies require full evaluation. The Performance Index (PI) provides a fast screening metric:

\[
\text{PI}_c = \sum_l \left(\frac{f_{l}^{(c)}}{\bar{F}_l}\right)^2
\tag{PI}
\]

Contingencies with PI below a threshold (default 1.0) are unlikely to cause overloads and can be skipped. The `screen_contingencies()` method returns a ranked list of the most critical contingencies.

### Transformer Contingencies

Transformers are modeled as branches with their own impedance and thermal rating. When a transformer trips, the B-matrix is rebuilt without that branch, identical to a line outage. Transformer reactance is computed from the tap ratio and short-circuit impedance.

### Battery Contingencies

Batteries actively discharging act as generation sources. When a discharging battery trips, the power deficit is redistributed among remaining generators using the same droop-based or pro-rata redistribution as generator contingencies. Only batteries with net injection > 0 are considered.

### N-1-1 (N-k) Analysis

Sequential contingency analysis: after the first element trips and power redistributes, a second contingency is evaluated on the stressed system. The `analyze_n1_1()` method:

1. Applies the first contingency → post-contingency state
2. Rebuilds a modified snapshot reflecting the new operating point
3. Applies the second contingency on the modified state

The `screen_n1_1()` method identifies the most critical pairs by first finding N-1 contingencies that stress lines above a threshold (default 80% loading), then testing those stressed lines as second contingencies.

### Droop-Based Generation Redistribution

When a generator trips, remaining generators can redistribute power using droop-based participation factors instead of simple pro-rata:

\[
\Delta P_g = \frac{(1/R_g) \cdot P^{rated}_g}{\sum_{g'} (1/R_{g'}) \cdot P^{rated}_{g'}} \cdot P^{lost}
\]

where \(R_g\) is the generator's droop coefficient. This matches the primary frequency response assumption from the frequency analysis module, providing consistent results between electrical and frequency N-1 assessments.

### Security-Constrained OPF (SCOPF)

When `scopf_enabled: true`, ESFEX replaces the preventive N-1 approach with an iterative SCOPF that adds constraints only for binding contingencies:

1. Solve base-case dispatch (no N-1 constraints)
2. Evaluate all N-1 contingencies using LODF
3. For each violated contingency, add: \(f_l + \text{LODF}_{l,k} \cdot f_k \leq \bar{F}_l\)
4. Re-solve until no new violations are found (max iterations configurable)

This is more efficient than the preventive approach because only a fraction of contingency constraints are typically binding.

### Corrective N-1 Actions

When `corrective_enabled: true`, post-contingency battery response is allowed:

- Base-case flows are limited to full line capacity (not reduced by reserve factor)
- Post-contingency, batteries can adjust charge/discharge to relieve overloads
- Generator output is also allowed to change within ramp limits

This produces less conservative results than preventive N-1, reflecting the reality that storage-rich systems can respond to contingencies within the frequency response timeframe.

### Integrated N-1 Assessment

The `IntegratedN1Analyzer` combines three security dimensions into a unified assessment:

| Dimension | Metric | Source |
|-----------|--------|--------|
| Thermal | Line overloads, load shedding | `ContingencyAnalyzer` / `ACContingencyAnalyzer` |
| Frequency | ROCOF, nadir | `FrequencyAnalyzer` |
| Voltage | Bus voltage violations | `NativeACBridge` / `PandapowerBridge` |

Each contingency receives a composite severity score:

\[
S = \underbrace{\max\_\text{overload}\%}_{\text{thermal}} + \underbrace{\frac{P^{shed}}{P^{demand}} \times 100}_{\text{load shed}} + \underbrace{(\Delta f^{lim} - f^{nadir}) \times 20}_{\text{frequency}} + \underbrace{\sum |V^{viol}| \times 100}_{\text{voltage}}
\]

### N-1 Results in HDF5

When N-1 security is active, the following data is exported to the HDF5 output file under each year's `n1_security/` group:

| Dataset | Shape | Description |
|---------|-------|-------------|
| `gen_reserve_duals` | `[hours]` | Dual of generation N-1 reserve constraint (USD/MW) |
| `trans_reserve_duals/` | Group | Duals of SCOPF transmission constraints per line/outage pair |
| `binding_contingencies` | String array | Names of binding contingency constraints |
| `security_cost` | Scalar attr | Incremental cost of N-1 security ($) |

### Configuration

```yaml
n1_security:
  enabled: true
  transmission_enabled: true
  transmission_reserve_factor: 0.70
  critical_threshold: 0.50
  generation_enabled: true
  reserve_type: largest_unit      # largest_unit, percentage, fixed
  reserve_percentage: 0.15
  scopf_enabled: false            # Use iterative SCOPF instead of preventive N-1
  scopf_max_iterations: 5         # Max SCOPF iterations
  scopf_violation_tolerance: 0.01 # MW tolerance for violation detection
  corrective_enabled: false       # Allow corrective post-contingency actions
```

### Computational Impact

N-1 constraints can significantly increase problem size:

| Metric | Without N-1 | Preventive N-1 (10 lines) | SCOPF (10 lines) |
|--------|-------------|---------------------------|-------------------|
| Constraints | ~10,000 | ~110,000 | ~15,000-50,000 |
| Variables | ~5,000 | ~5,000 (same) | ~5,000 (same) |
| Solve time | 1× | 5-15× | 2-8× |

To manage computational cost:
- Use SCOPF instead of preventive N-1 (adds only binding constraints)
- Increase `critical_threshold` to test fewer contingencies
- Use `screen_contingencies()` with PI threshold to pre-filter
- Apply N-1 only in the master problem (representative days) rather than every operational window

Implemented in `add_n1_security_constraints!()` and `add_scopf_constraints!()` in `power_system.jl`. Post-optimization analysis in `contingency.py` and `n1_assessment.py`.

---


## Example: 3-Bus System

A simple 3-bus example illustrates the DC power flow formulation:

```
    Bus 1 (Slack, Gen=200MW)
     / \
  L1/   \L2
   /     \
Bus 2     Bus 3
(D=100MW) (D=80MW, Gen=50MW)
    \     /
   L3\   /
      \ /
```

**Network data:**

| Line | From | To | Reactance (pu) | Capacity (MW) |
|------|------|----|-----------------|---------------|
| L1 | 1 | 2 | 0.10 | 150 |
| L2 | 1 | 3 | 0.15 | 100 |
| L3 | 2 | 3 | 0.20 | 80 |

**Incidence matrix K:**

|  | L1 | L2 | L3 |
|--|----|----|-----|
| Bus 1 | +1 | +1 | 0 |
| Bus 2 | -1 | 0 | +1 |
| Bus 3 | 0 | -1 | -1 |

**One independent cycle:** L1 → L3 → L2 (via spanning tree), giving cycle matrix C = [+1, -1, +1]ᵀ

**KVL constraint:** `0.10·f₁ - 0.15·f₂ + 0.20·f₃ = 0`

**KCL at each bus:**
- Bus 1: 200 - 0 = f₁ + f₂ → f₁ + f₂ = 200 (generation minus demand)
- Bus 2: 0 - 100 = -f₁ + f₃ → f₃ - f₁ = -100
- Bus 3: 50 - 80 = -f₂ - f₃ → f₂ + f₃ = 30 (net demand = 30 MW)

**Solution:** f₁ ≈ 96.7 MW, f₂ ≈ 33.3 MW, f₃ ≈ -3.3 MW (reverse flow on L3)


---


## AC Power Flow Verification

The DC power flow approximation is computationally efficient and well-suited for optimization, but it neglects voltage magnitude variations and reactive power. ESFEX provides an optional post-DC AC power flow verification step that runs a full Newton-Raphson solver on the DC-OPF dispatch solution, validating that the operating point is physically feasible under the full AC power flow equations.

### Purpose

The AC verification serves as a validation layer, not a re-optimization:

1. **Voltage profile validation** — Verify that bus voltage magnitudes remain within acceptable bounds (typically 0.90–1.10 p.u.) under the dispatch solution determined by the DC-OPF.
2. **Reactive power assessment** — Compute reactive power flows and generator reactive power output, which are invisible to the DC approximation.
3. **Accurate loss computation** — Calculate true \(I^2R\) + \(I^2X\) losses on each branch, including both active and reactive components.
4. **Thermal limit verification** — Check branch MVA flows against thermal ratings using the full apparent power (not just active power as in DC).
5. **Violation detection** — Flag voltage violations and line overloads that the DC approximation may miss, providing feedback for the planner.

### Dual Implementation

ESFEX offers two AC power flow implementations:

| Implementation | File | Engine | Use Case |
|---------------|------|--------|----------|
| Native Julia NR | `transmission_ac.jl` | Custom Newton-Raphson (polar form) | Integrated post-dispatch verification |
| Pandapower bridge | `pandapower_bridge.py` | pandapower Newton-Raphson | GUI analysis, IEC 60909 short-circuit |

#### Julia Newton-Raphson (`transmission_ac.jl`)

The native solver implements the standard Newton-Raphson method in polar coordinates:

- **Y-bus construction** from line impedances (π-model) and transformer models (with tap ratio)
- **Bus classification**: Slack (reference, known \(|V|\) and \(\theta\)), PV (generators, known \(P\) and \(|V|\)), PQ (loads, known \(P\) and \(Q\))
- **Jacobian**: 4-submatrix structure \(\mathbf{J} = [H, N; M, L]\) where \(H = \partial P/\partial\theta\), \(N = \partial P/\partial|V|\), \(M = \partial Q/\partial\theta\), \(L = \partial Q/\partial|V|\)
- **Convergence**: \(\max(|\Delta P|, |\Delta Q|) < \epsilon\) (default \(\epsilon = 10^{-6}\))
- **Initialization**: DC voltage angles as warm-start for \(\theta\), flat start (1.0 p.u.) for \(|V|\)

The solver takes the DC-OPF dispatch result and network data as input, and returns per-bus voltages, per-branch active/reactive power flows, total losses, voltage violations, and line overloads.

#### Native AC Power Flow Bridge (`native_ac_bridge.py`)

The native AC bridge uses the Julia Newton-Raphson solver (`transmission_ac.jl`) directly from the GUI, without requiring external dependencies. It converts the Studio state (`GuiSystemState`) into flat arrays, passes them to Julia via `solve_gui_ac_power_flow()`, and returns results in the same `ACPowerFlowResult` format used throughout the analysis pipeline.

The bridge implements the same duck-typed interface as the pandapower bridge, making it a drop-in replacement for AC power flow and N-1 contingency analysis. The GUI prefers the native bridge when available and falls back to pandapower automatically.

#### Pandapower Bridge (`pandapower_bridge.py`)

The pandapower bridge is now used exclusively for IEC 60909 short-circuit analysis (`ikss`, `ip`, `sk`) via `pandapower.shortcircuit.calc_sc()`. AC power flow has been migrated to the native Julia solver, reducing the external dependency footprint.

### AC Contingency Analysis

The `ACContingencyAnalyzer` extends N-1 security analysis to the AC domain:

- For each contingency (generator or line loss), the element is taken out of service in the bridge network
- For generator loss: output is redistributed pro-rata to remaining dispatchable generators based on headroom
- AC power flow is re-solved; if it diverges, the analysis falls back to DC contingency results
- Post-contingency results include voltage magnitudes, reactive power, line loading percentages, and voltage violation detection
- The analyzer is backend-agnostic: it works with both `NativeACBridge` (default) and `PandapowerBridge` through duck typing

### Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_iterations` | 50 | Maximum Newton-Raphson iterations |
| `tolerance` | 1e-6 | Convergence tolerance on power mismatch |
| `base_mva` | 100.0 | System base power for per-unit conversion |
| `voltage_min_pu` | 0.90 | Lower voltage magnitude limit |
| `voltage_max_pu` | 1.10 | Upper voltage magnitude limit |

### References

The DC power flow approximation is formally analyzed by Stott et al. [**[1]**](../reference/bibliography.md#ref1). The general power systems analysis framework follows Glover et al. [**[41]**](../reference/bibliography.md#ref41). PTDF and LODF factors for fast contingency screening follow Van Hertem et al. [**[42]**](../reference/bibliography.md#ref42) and Guler et al. [**[43]**](../reference/bibliography.md#ref43). Security-constrained OPF with post-contingency corrective actions follows Capitanescu et al. [**[34]**](../reference/bibliography.md#ref34) and Monticelli et al. [**[44]**](../reference/bibliography.md#ref44). Performance index-based contingency screening follows Ejebe and Wollenberg [**[45]**](../reference/bibliography.md#ref45). AC power flow verification uses Newton-Raphson as implemented in MATPOWER [**[46]**](../reference/bibliography.md#ref46) and pandapower [**[47]**](../reference/bibliography.md#ref47).

See the [full bibliography](../reference/bibliography.md) for complete citation details.
