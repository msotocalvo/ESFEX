# Frequency Stability Analysis

The frequency stability module computes post-contingency frequency metrics using the center-of-inertia (COI) model [**[5]**](../reference/bibliography.md#ref5), [**[6]**](../reference/bibliography.md#ref6). All calculations are algebraic -- no time-domain simulation or differential equation solving is required.

## Overview

When a generator trips, the power imbalance causes the system frequency to deviate from its nominal value (50 Hz or 60 Hz). The frequency response passes through three phases:

1. **Inertial response** (0--2 s): Stored kinetic energy in rotating machines resists frequency change. The rate of change of frequency (ROCOF) depends on the total system inertia.
2. **Primary frequency response** (2--30 s): Governor droop control adjusts generator output proportionally to the frequency deviation. The frequency reaches its lowest point (nadir) and begins recovering.
3. **Quasi-steady-state** (30 s+): The frequency settles at a new steady-state value below nominal, determined by the aggregate droop characteristic.

The COI model aggregates all synchronous machines into a single equivalent machine, enabling closed-form analytical solutions for each metric.

## Mathematical Model

### System Inertia

The aggregate system inertia \(H_{\text{sys}}\) is the sum of individual machine inertia constants weighted by their output:

\[
H_{\text{sys}} = \sum_{i \in \mathcal{G}_{\text{on}}} H_i \cdot P_i \quad \text{[MW·s]}
\tag{FS-1}
\]

where:
- \(H_i\) is the inertia constant of generator \(i\) (seconds)
- \(P_i\) is the current output of generator \(i\) (MW)
- \(\mathcal{G}_{\text{on}}\) is the set of online generators

### Aggregate Damping

The total system damping \(D_{\text{total}}\) combines load damping and generator droop response:

\[
D_{\text{total}} = D_{\text{load}} + D_{\text{droop}} \quad \text{[MW/Hz]}
\tag{FS-2}
\]

where:

\[
D_{\text{load}} = \frac{D_L \cdot P_{\text{demand}}}{f_{\text{nom}}}
\tag{FS-3}
\]

\[
D_{\text{droop}} = \sum_{i \in \mathcal{G}_{\text{on}}} \frac{P_{\text{rated},i}}{R_i \cdot f_{\text{nom}}}
\tag{FS-4}
\]

- \(D_L\) is the load damping coefficient (pu), typically 0.01--0.02
- \(P_{\text{demand}}\) is the total system demand (MW)
- \(R_i\) is the governor droop of generator \(i\) (pu), typically 0.04--0.06
- \(P_{\text{rated},i}\) is the rated power of generator \(i\) (MW)
- \(f_{\text{nom}}\) is the nominal frequency (Hz)

Renewable generators (solar, wind) do not contribute to \(D_{\text{droop}}\) unless equipped with synthetic inertia or droop control.

### Rate of Change of Frequency (ROCOF)

The initial ROCOF immediately after the contingency event:

\[
\text{ROCOF} = \frac{\Delta P \cdot f_{\text{nom}}}{2 \cdot H_{\text{sys}}} \quad \text{[Hz/s]}
\tag{FS-5}
\]

where \(\Delta P\) is the power imbalance (MW). A typical protection relay threshold is 0.5--2.0 Hz/s, above which generators may trip due to loss-of-mains protection.

### Frequency Nadir

The lowest frequency point, derived from the linearized swing equation:

\[
\Delta f_{\text{nadir}} = \frac{\Delta P}{2 \sqrt{H_{\text{sys}} \cdot D_{\text{total}}}}
\tag{FS-6}
\]

\[
f_{\text{nadir}} = f_{\text{nom}} - \Delta f_{\text{nadir}} \quad \text{[Hz]}
\tag{FS-7}
\]

Under-frequency load shedding (UFLS) is typically triggered at 49.0 Hz (for 50 Hz systems) or 59.0 Hz (for 60 Hz systems).

### Time to Nadir

The time from the contingency event to the frequency nadir:

\[
t_{\text{nadir}} = \pi \sqrt{\frac{H_{\text{sys}}}{D_{\text{total}}}} \quad \text{[s]}
\tag{FS-8}
\]

### Steady-State Frequency

The post-primary-response steady-state frequency:

\[
\Delta f_{\text{ss}} = \frac{\Delta P}{D_{\text{total}}}
\tag{FS-9}
\]

\[
f_{\text{ss}} = f_{\text{nom}} - \Delta f_{\text{ss}} \quad \text{[Hz]}
\tag{FS-10}
\]

## Configuration Parameters

The frequency analysis uses parameters from the system configuration:

| Parameter | Location | Default | Description |
|-----------|----------|---------|-------------|
| `inertia` | `GeneratorConfig` / `TechnologyConfig` | 0.0 s | Inertia constant H per node (already existing) |
| `droop` | `GeneratorConfig` / `TechnologyConfig` | 0.05 pu | Governor droop characteristic (5%) |
| `governor_time_const` | `GeneratorConfig` / `TechnologyConfig` | 5.0 s | Governor time constant |
| `load_damping` | `SystemConfig` | 0.01 pu | Load damping coefficient D |
| `frequency_nominal` | `SystemConfig` | 50.0 Hz | Nominal system frequency |
| `rocof_limit` | `SystemConfig` | 2.0 Hz/s | Maximum allowable ROCOF |
| `frequency_nadir_limit` | `SystemConfig` | 49.0 Hz | Minimum allowable frequency |

Droop and governor time constant are per-node arrays (like other generator parameters), allowing different settings for each generator at each node.

## N-1 Frequency Screening

The `analyze_all_n1()` method performs frequency analysis for the loss of each online generator:

1. For each online generator with non-zero output, set \(\Delta P\) = generator output
2. Compute ROCOF, nadir, steady-state frequency using the formulas above
3. Sort results by severity (lowest nadir first)
4. Flag contingencies where nadir < `frequency_nadir_limit` or ROCOF > `rocof_limit`

This provides a rapid screening of all N-1 generator contingencies without re-running the optimization.

## N-1 Contingency Analysis (DC Power Flow)

For more detailed contingency analysis, the `ContingencyAnalyzer` uses DC power flow to compute post-contingency line flows and detect overloads.

### Generator Loss

When a generator trips:

1. Its output is redistributed pro-rata among remaining generators based on available headroom (capacity - current output)
2. If total headroom is insufficient, load shedding is applied proportionally to demand at each node
3. The DC power flow is solved with updated nodal injections to find new line flows
4. Lines exceeding their thermal capacity are flagged as overloaded

### Line Loss

When a transmission line trips:

1. The bus susceptance matrix B is rebuilt without the tripped line
2. New voltage angles are computed: \(\boldsymbol{\theta} = \mathbf{B}^{-1} \cdot \mathbf{P}_{\text{inj}} / S_{\text{base}}\)
3. New line flows are computed: \(f_l = (\theta_i - \theta_j) / x_l \cdot S_{\text{base}}\)
4. Lines exceeding their thermal capacity are flagged as overloaded

The susceptance matrix is defined as:

\[
B_{ij} = \begin{cases}
-1/x_{ij} & i \neq j, \text{ line between } i \text{ and } j \\
\sum_{k \neq i} 1/x_{ik} & i = j
\end{cases}
\tag{FS-11}
\]

The slack bus row and column are removed before matrix inversion.

## Assumptions and Limitations

1. **Linearized swing equation**: The nadir formula (FS-6) assumes small frequency deviations [**[5]**](../reference/bibliography.md#ref5). For large deviations (> 2 Hz), the nonlinear swing equation would be more accurate.
2. **Uniform system frequency**: The COI model assumes all machines see the same frequency deviation. In reality, inter-area oscillations can cause localized frequency variations.
3. **No governor delays**: The model uses the aggregate droop characteristic without modeling individual governor dynamics (ramp rates, dead bands).
4. **No secondary/tertiary response**: Only primary frequency response (droop control) is modeled. AGC and manual actions are not considered.
5. **DC power flow**: Line loss contingency analysis uses DC approximation (lossless, linear). This may underestimate flows on heavily loaded or long lines.

## Implementation

| File | Purpose |
|------|---------|
| `src/esfex/analysis/frequency.py` | `FrequencyAnalyzer` class with ROCOF, nadir, steady-state calculations |
| `src/esfex/analysis/contingency.py` | `ContingencyAnalyzer` class with DC power flow contingency analysis |
| `src/esfex/analysis/__init__.py` | Package exports |
| `src/esfex/config/schema.py` | `droop`, `governor_time_const`, `load_damping` configuration fields |

## References

The COI model and swing equation formulation follow Kundur [**[5]**](../reference/bibliography.md#ref5) (Ch. 11) and Anderson and Fouad [**[6]**](../reference/bibliography.md#ref6). ROCOF protection thresholds and frequency criteria are based on ENTSO-E [**[59]**](../reference/bibliography.md#ref59). The effective inertia framework follows Ela et al. [**[60]**](../reference/bibliography.md#ref60) and Ulbig et al. [**[61]**](../reference/bibliography.md#ref61). Governor droop and primary frequency response are detailed in Wood et al. [**[31]**](../reference/bibliography.md#ref31) (Ch. 9).

See the [full bibliography](../reference/bibliography.md) for complete citation details.
