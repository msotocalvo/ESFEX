# Electrolyzer Model

The electrolyzer model implements power-to-hydrogen (P2H2) conversion [**[49]**](../reference/bibliography.md#ref49), coupling the electrical power system with hydrogen production for sector coupling applications [**[50]**](../reference/bibliography.md#ref50). It is implemented in `electrolyzer.jl`.

---


## 1. Overview


Electrolyzers consume electricity to produce hydrogen via water electrolysis (splitting water molecules into hydrogen and oxygen). In power system planning, electrolyzers serve dual roles:

1. **Flexible load**: Electrolyzers can modulate their power consumption to absorb surplus renewable generation, providing demand-side flexibility.
2. **Sector coupling**: Hydrogen output can satisfy non-electric energy demands (industrial feedstock, transport fuel, heating) or be stored for later reconversion to electricity via fuel cells.

The model supports:

- Capacity investment decisions for new electrolyzer installations
- Variable efficiency modeling based on operating load
- Ramp rate constraints reflecting physical limitations
- Coupling with both the power system (electricity consumption) and the primary energy module (hydrogen output)
- Water consumption tracking

---


## 2. Technology Background

### 2.1 Electrolyzer Types


The model is technology-agnostic but parameterized to represent common electrolyzer technologies:

| Technology | Typical Efficiency | Ramp Rate | Capital Cost | Lifetime |
|------------|-------------------|-----------|-------------|----------|
| Alkaline (AEL) | 60--70% | Moderate | Lower | 20--30 years |
| PEM (PEMEL) | 55--70% | Fast | Higher | 10--20 years |
| Solid Oxide (SOEL) | 70--85% | Slow | Highest | 5--10 years |

The efficiency values represent the ratio of hydrogen energy output (HHV) to electrical energy input.

### 2.2 Energy Content of Hydrogen


The energy required to produce hydrogen depends on the thermodynamic pathway. Key reference values:

| Property | Value |
|----------|-------|
| Lower Heating Value (LHV) | 33.33 kWh/kg |
| Higher Heating Value (HHV) | 39.41 kWh/kg |
| Thermodynamic minimum | 39.41 kWh/kg (reversible, HHV) |
| Practical consumption | 50--60 kWh/kg (including system losses) |

The model parameter `energy_per_kg_h2` captures the total system-level energy consumption per kilogram of hydrogen produced.

---


## 3. Decision Variables


| Variable | Domain | Units | Description | Julia Name |
|----------|--------|-------|-------------|------------|
| \(I^{elz}_n\) | \(\mathbb{R}_+\) | MW | Electrolyzer capacity investment at node \(n\) | `elz_investment[n]` |
| \(P^{elz}_{n,t}\) | \(\mathbb{R}_+\) | MW | Power consumption at node \(n\), hour \(t\) | `electrolyzer_power[n,t]` |
| \(H_{n,t}\) | \(\mathbb{R}_+\) | kg/h | Hydrogen production at node \(n\), hour \(t\) | `hydrogen_production[n,t]` |

Built by `build_electrolyzer_variables!()`.

---


## 4. Constraints

### ELZ-1: Power Capacity Limit

Electrolyzer power consumption is bounded by the sum of existing rated capacity and new investment:

\[
P^{elz}_{n,t} \leq \bar{P}^{elz}_n + I^{elz}_n \quad \forall n, t
\tag{ELZ-1}
\]

where:

- \(\bar{P}^{elz}_n\) is the existing rated electrolyzer capacity at node \(n\) (MW)
- \(I^{elz}_n\) is the investment variable (MW), bounded by `invest_max_power[n]`

This constraint ensures that power consumption never exceeds total installed capacity. The investment variable is bounded by:

\[
0 \leq I^{elz}_n \leq \bar{I}^{elz}_n \qquad \forall n
\]

where \(\bar{I}^{elz}_n\) is the maximum allowable investment at node \(n\).

### ELZ-2: Hydrogen Production

Hydrogen production is proportional to power consumption via an average efficiency:

\[
H_{n,t} = P^{elz}_{n,t} \times \frac{1000 \cdot \bar{\eta}^{elz}}{E_{H_2}} \quad \forall n, t
\tag{ELZ-2}
\]

where:

- \(\bar{\eta}^{elz} = (\eta^{rated} + \eta^{min}) / 2\) is the average efficiency over the operating range
- \(E_{H_2}\) is the energy per kilogram of hydrogen (kWh/kg), from `energy_per_kg_h2`
- The factor 1000 converts MW to kW for unit consistency

**Derivation of the average efficiency.** Electrolyzer efficiency varies with load: it is typically highest at partial load and decreases at rated power due to increased overpotentials. The true efficiency curve \(\eta(P)\) is nonlinear. Rather than introducing a piecewise-linear approximation (which would add complexity), the model uses the arithmetic mean of the efficiencies at rated power and minimum operating point:

\[
\bar{\eta}^{elz} = \frac{\eta^{rated} + \eta^{min}}{2}
\]

For example, with \(\eta^{rated} = 0.70\) and \(\eta^{min} = 0.65\), the average efficiency is 0.675.

!!! note "Efficiency Approximation"
    The model uses a constant average efficiency rather than a piecewise-linear efficiency curve. This linearization avoids additional variables and constraints while providing a reasonable approximation for capacity planning studies. For detailed operational studies where load-dependent efficiency significantly impacts results, a PWL efficiency curve could be implemented using the same segment decomposition approach used for generator fuel cost curves (see [Operational Dispatch -- PWL](operational-dispatch.md#311-piecewise-linear-pwl-fuel-cost-decomposition)).

### ELZ-3: Ramp Rate Constraints

Power consumption changes between consecutive time steps are limited by ramp rates, reflecting the physical inertia of the electrolysis process (thermal management, gas handling, membrane conditioning):

\[
P^{elz}_{n,t} - P^{elz}_{n,t-1} \leq (\bar{P}^{elz}_n + I^{elz}_n) \cdot r^{up}_{n} \quad \forall n, t > 1
\tag{ELZ-3a}
\]

\[
P^{elz}_{n,t-1} - P^{elz}_{n,t} \leq (\bar{P}^{elz}_n + I^{elz}_n) \cdot r^{down}_{n} \quad \forall n, t > 1
\tag{ELZ-3b}
\]

where:

- \(r^{up}_n \in [0,1]\) is the maximum ramp-up rate (fraction of total capacity per hour)
- \(r^{down}_n \in [0,1]\) is the maximum ramp-down rate (fraction of total capacity per hour)

Typical ramp rates range from 0.1 (solid oxide, slow thermal response) to 1.0 (PEM, can ramp from zero to full power in under a minute, effectively unconstrained at hourly resolution).

Implemented in `add_electrolyzer_constraints!()`.

### ELZ-4: Minimum Operating Point (Optional)

When configured, the electrolyzer has a minimum stable operating point below which it must shut down:

\[
P^{elz}_{n,t} \geq \underline{P}^{elz}_n \cdot u^{elz}_{n,t} \quad \forall n, t
\]

where \(\underline{P}^{elz}_n\) is the minimum power fraction and \(u^{elz}_{n,t}\) is a binary on/off status. In the LP formulation, this constraint is relaxed or omitted to maintain linearity.

---


## 5. Objective Terms


The electrolyzer contributes the following cost terms to the objective function, implemented in `get_electrolyzer_objective_terms()`:

\[
C^{elz} = \sum_n \left[ C^{elz,inv}_n + C^{elz,fix}_n + C^{elz,var}_n + C^{elz,water}_n \right]
\tag{ELZ-OBJ}
\]

### 5.1 Annualized Investment Cost


Investment cost is annualized over the electrolyzer lifetime and prorated to the optimization window:

\[
C^{elz,inv}_n = \frac{c^{inv,elz}_n}{\tau^{elz}_n \cdot 8760} \cdot I^{elz}_n \cdot T
\]

where:

- \(c^{inv,elz}_n\) is the total investment cost (\$/MW)
- \(\tau^{elz}_n\) is the lifetime (years)
- \(T\) is the number of time steps in the optimization window
- The factor \(8760\) converts annual cost to hourly

### 5.2 Fixed O&M Cost


Fixed operations and maintenance cost, proportional to total installed capacity:

\[
C^{elz,fix}_n = c^{fix,elz}_n \cdot (\bar{P}^{elz}_n + I^{elz}_n) \cdot T
\]

where \(c^{fix,elz}_n\) is the fixed O&M cost rate (\$/MW/hour).

### 5.3 Variable O&M Cost


Variable cost proportional to electricity consumed:

\[
C^{elz,var}_n = \sum_t c^{var,elz}_n \cdot P^{elz}_{n,t}
\]

where \(c^{var,elz}_n\) is the variable O&M cost (\$/MWh).

### 5.4 Water Cost


Water consumption cost proportional to hydrogen produced:

\[
C^{elz,water}_n = \sum_t c^{water} \cdot H_{n,t}
\]

where \(c^{water}\) is the water cost per kilogram of hydrogen (\$/kg H2). Water consumption for electrolysis is approximately 9 liters per kg H2.

### 5.5 Cost Summary Table


| Term | Formula | Units | Description |
|------|---------|-------|-------------|
| Annualized investment | \(\frac{c^{inv}}{\tau \cdot 8760} \cdot I \cdot T\) | \$ | Capital cost spread over lifetime |
| Fixed O&M | \(c^{fix} \cdot (\bar{P} + I) \cdot T\) | \$ | Capacity-proportional maintenance |
| Variable O&M | \(\sum_t c^{var} \cdot P_t\) | \$ | Energy-proportional operating cost |
| Water | \(\sum_t c^{water} \cdot H_t\) | \$ | Feedstock cost |

---


## 6. Power System Coupling


The electrolyzer is coupled to the power system as an additional electrical load. Electrolyzer power consumption appears on the demand side of the power balance equation:

**Single-bus (PB-1):**

\[
\sum_g P_{g,n,t} + \ldots = D_{n,t} + P^{elz}_{n,t} + \sum_b P^{ch}_{b,n,t} + \ldots
\]

**Multi-bus (DC-1):**

\[
\text{net\_inj}_{n,t} = \sum_g P_{g,n,t} - D_{n,t} - P^{elz}_{n,t} - \ldots = \sum_\ell K_{n,\ell} f_{\ell,t}
\]

This coupling is handled by `couple_electrolyzer_to_power_system!()`. The electrolyzer power variable \(P^{elz}_{n,t}\) is added to the demand-side expressions at the node where the electrolyzer is located.

---


## 7. Hydrogen Demand Coupling


When the primary energy module is active, hydrogen produced by the electrolyzer can satisfy hydrogen demand requirements:

\[
\sum_t H_{n,t} \cdot \Delta t \geq D^{H_2}_{n} \quad \forall n
\]

where \(D^{H_2}_n\) is the hydrogen demand at node \(n\) over the optimization window (kg). This constraint links the electrolyzer to the broader energy system, ensuring that hydrogen production meets industrial, transport, or other non-electric hydrogen demands.

If hydrogen storage is available, the temporal profile of production can differ from the demand profile, allowing the electrolyzer to operate flexibly:

\[
V^{H_2}_{n,t+1} = V^{H_2}_{n,t} + H_{n,t} \cdot \Delta t - W^{H_2}_{n,t}
\]

where \(V^{H_2}_{n,t}\) is the hydrogen inventory and \(W^{H_2}_{n,t}\) is the hydrogen withdrawal for end-use.

---


## 8. Operational Strategies


The optimizer naturally selects the electrolyzer operating strategy that minimizes total system cost:

| Strategy | Condition | Behavior |
|----------|-----------|----------|
| **Load following RE** | High RE penetration | Electrolyzer absorbs surplus RE, reducing curtailment |
| **Baseload** | Low electricity prices | Continuous operation at high capacity factor |
| **Peak shaving** | High peak demand | Electrolyzer reduces consumption during peaks |
| **Idle** | High electricity prices | Electrolyzer shuts down, hydrogen demand unmet (penalized) |

The flexibility of the electrolyzer is bounded by ramp rate constraints (ELZ-3) and minimum operating point (if configured). Faster-ramping technologies (PEM) can better follow variable renewable generation.

---


## 9. Configuration


Electrolyzer parameters are specified per node in the system configuration:

```yaml
electrolyzers:
  alkaline:
    name: Alkaline Electrolyzer
    rated_power: [10.0]           # MW per node
    eff_at_rated: [0.70]          # Efficiency at rated power
    eff_at_min: [0.65]            # Efficiency at minimum power
    energy_per_kg_h2: 55.0        # kWh per kg H2 (system-level)
    ramp_up: [0.5]                # Fraction of capacity per hour
    ramp_down: [0.5]
    invest_cost: [1500000.0]      # $/MW total investment
    invest_max_power: [50.0]      # MW maximum investment per node
    fixed_cost: [30000.0]         # $/MW/year fixed O&M
    variable_cost: [2.0]          # $/MWh variable O&M
    water_cost: 0.05              # $/kg H2
    life_time: [20]               # years
```

### 9.1 Parameter Reference


| Parameter | Symbol | Units | Description |
|-----------|--------|-------|-------------|
| `rated_power` | \(\bar{P}^{elz}_n\) | MW | Existing installed capacity per node |
| `eff_at_rated` | \(\eta^{rated}\) | -- | Efficiency at rated power |
| `eff_at_min` | \(\eta^{min}\) | -- | Efficiency at minimum operating point |
| `energy_per_kg_h2` | \(E_{H_2}\) | kWh/kg | System energy consumption per kg H2 |
| `ramp_up` | \(r^{up}\) | 1/h | Maximum ramp-up rate (fraction of capacity) |
| `ramp_down` | \(r^{down}\) | 1/h | Maximum ramp-down rate (fraction of capacity) |
| `invest_cost` | \(c^{inv,elz}\) | \$/MW | Total investment cost |
| `invest_max_power` | \(\bar{I}^{elz}\) | MW | Maximum investment per node |
| `fixed_cost` | \(c^{fix,elz}\) | \$/MW/yr | Annual fixed O&M cost |
| `variable_cost` | \(c^{var,elz}\) | \$/MWh | Variable O&M cost |
| `water_cost` | \(c^{water}\) | \$/kg H2 | Water feedstock cost |
| `life_time` | \(\tau^{elz}\) | years | Equipment lifetime |

---


## 10. Implementation Reference


| Julia Function | Purpose | File |
|----------------|---------|------|
| `build_electrolyzer_variables!()` | Create investment, power, and production variables | `electrolyzer.jl` |
| `add_electrolyzer_constraints!()` | Add ELZ-1 through ELZ-3 constraints | `electrolyzer.jl` |
| `get_electrolyzer_objective_terms()` | Compute objective cost contributions | `electrolyzer.jl` |
| `couple_electrolyzer_to_power_system!()` | Add electrolyzer power to demand side of balance | `electrolyzer.jl` |
| `extract_electrolyzer_solution()` | Extract power, production, and investment results | `electrolyzer.jl` |

---

## References

The power-to-hydrogen conversion modeling and electrolysis technology review follow Buttler and Spliethoff [**[49]**](../reference/bibliography.md#ref49). The economics of renewable hydrogen production are analyzed by Glenk and Reichelstein [**[50]**](../reference/bibliography.md#ref50).

See the [full bibliography](../reference/bibliography.md) for complete citation details.
