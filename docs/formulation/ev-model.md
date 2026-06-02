# EV Model

The electric vehicle (EV) model integrates fleet electrification into power system planning and operation with S-curve adoption dynamics and vehicle-to-grid (V2G) [**[51]**](../reference/bibliography.md#ref51) capability. The model captures the dual role of EV fleets as flexible electrical loads and distributed energy storage resources [**[52]**](../reference/bibliography.md#ref52).

## Overview

The EV model consists of two components:

1. **Python-side** (`models/ev.py`): Fleet growth modeling with logistic S-curves, multi-category vehicle definitions, and charging profile generation for each planning year.
2. **Julia-side** (`power_system.jl`): Optimization constraints for EV charging and V2G dispatch within the operational power system model.

The interaction between these components follows the general ESFEX data flow: the Python layer generates exogenous fleet projections and charging demand profiles, which are then passed to the Julia optimizer as parameters. The optimizer determines the optimal charging schedule and V2G dispatch subject to grid constraints.

---


## 1. S-Curve Fleet Adoption


EV fleet growth follows a logistic (S-curve) function that captures the characteristic adoption pattern of new technologies: slow initial uptake, rapid mid-phase growth, and eventual market saturation.

\[
N_{EV}(y) = N_0 \cdot \frac{K}{1 + e^{-r(y - y_{mid})}}
\tag{EV-S}
\]

where:

| Parameter | Description | Config Field | Typical Range |
|-----------|-------------|-------------|---------------|
| \(N_0\) | Initial fleet size (vehicles) | `ev_quantity` | System-specific |
| \(K\) | Maximum adoption multiplier | `max_adoption` | 2--20 |
| \(r\) | Logistic growth rate | `growth_rate` | 0.1--0.5 |
| \(y_{mid}\) | Inflection year (steepest growth) | Computed from `mid_point_fraction` | Mid-horizon |

The midpoint year \(y_{mid}\) is computed as:

\[
y_{mid} = 1 + \text{mid\_point\_fraction} \times (Y - 1)
\]

where \(Y\) is the total number of planning years. A `mid_point_fraction` of 0.5 places the inflection at the midpoint of the planning horizon.

**Properties of the logistic curve:**

- At \(y = y_{mid}\): \(N_{EV} = N_0 \cdot K / 2\) (half of maximum adoption)
- As \(y \to \infty\): \(N_{EV} \to N_0 \cdot K\) (saturation)
- Growth rate is steepest at the inflection point, with slope \(N_0 \cdot K \cdot r / 4\)

This produces a smooth, monotonically increasing growth trajectory that avoids the unrealistic linearity of constant annual growth assumptions.

Implemented in `generate_ev_profiles()`.

---


## 2. Multi-Category Vehicle Definitions


Multiple vehicle categories can be defined, each with distinct technical and economic characteristics. This enables modeling of heterogeneous fleets where, for example, private sedans and commercial vehicles have different battery sizes, charging infrastructure, and usage patterns.

```yaml
ev_categories:
  sedan:
    battery_capacity_kwh: 60.0
    max_charge_power_kw: 11.0
    max_discharge_power_kw: 7.0
    charge_efficiency: 0.95
    discharge_efficiency: 0.92
    min_soc: 0.20
    max_soc: 0.90
    v2g_compensation: 0.05        # $/kWh

  commercial_van:
    battery_capacity_kwh: 100.0
    max_charge_power_kw: 22.0
    max_discharge_power_kw: 15.0
    charge_efficiency: 0.93
    discharge_efficiency: 0.90
    min_soc: 0.25
    max_soc: 0.85
    v2g_compensation: 0.07

ev_quantity:
  sedan: [1000, 1200, 1500]       # Initial count per node
  commercial_van: [200, 250, 300]
```

### 2.1 Category Parameters


| Parameter | Symbol | Units | Description |
|-----------|--------|-------|-------------|
| `battery_capacity_kwh` | \(E^{bat}_{cat}\) | kWh | Per-vehicle battery capacity |
| `max_charge_power_kw` | \(\bar{P}^{ch}_{cat}\) | kW | Maximum charging power per vehicle |
| `max_discharge_power_kw` | \(\bar{P}^{dis}_{cat}\) | kW | Maximum V2G discharge power per vehicle |
| `charge_efficiency` | \(\eta^{ch}_{cat}\) | -- | Round-trip charging efficiency |
| `discharge_efficiency` | \(\eta^{dis}_{cat}\) | -- | V2G discharging efficiency |
| `min_soc` | \(\underline{SOC}_{cat}\) | -- | Minimum allowable SOC fraction |
| `max_soc` | \(\overline{SOC}_{cat}\) | -- | Maximum allowable SOC fraction |

### 2.2 Fleet Aggregation


When multiple categories are present, their parameters are aggregated to produce nodal fleet-level quantities for the optimizer. The aggregation is weighted by the number of vehicles in each category:

\[
E^{total}_{EV,n} = \sum_{cat} \frac{N_{cat,n} \cdot E^{bat}_{cat}}{1000} \quad \text{[MWh]}
\]

\[
\bar{P}^{ch,total}_{n} = \sum_{cat} \frac{N_{cat,n} \cdot \bar{P}^{ch}_{cat}}{1000} \quad \text{[MW]}
\]

\[
\bar{P}^{dis,total}_{n} = \sum_{cat} \frac{N_{cat,n} \cdot \bar{P}^{dis}_{cat}}{1000} \quad \text{[MW]}
\]

The division by 1000 converts from kW/kWh to MW/MWh.

---


## 3. Charging Profiles


Base charging patterns (24-hour templates) define typical daily EV energy consumption behavior. These templates capture when vehicles are likely to be plugged in and drawing power:

```yaml
base_patterns:
  commuter: [0.1, 0.1, 0.1, 0.05, 0.05, 0.1, 0.3, 0.5, 0.2, 0.1, 0.1, 0.1,
             0.1, 0.1, 0.1, 0.2, 0.4, 0.8, 0.9, 0.7, 0.5, 0.3, 0.2, 0.1]
```

These patterns are processed as follows:

1. **Scaled by fleet size** \(N_{EV}(y)\) for each planning year
2. **Multiplied by per-vehicle charging power** to obtain aggregate demand in MW
3. **Aggregated across categories** and distributed to nodes based on fleet composition
4. **Extended to annual profiles** by repeating the 24-hour template across all days

The resulting EV charging demand profile \(D^{EV}_{n,t}\) represents the minimum energy that must be delivered to satisfy driving requirements. The optimizer may shift this charging temporally (smart charging) within the constraints defined below.

---


## 4. Optimization Constraints


The following constraints are added to the operational dispatch model by `add_ev_constraints!()` in `power_system.jl`. They model the EV fleet at each node as an aggregated flexible load with storage capability.

### EV-1: SOC Dynamics

The fleet-level state of charge evolves according to the energy balance of charging, discharging, and driving consumption:

\[
E^{EV}_{n,t+1} = E^{EV}_{n,t} + \eta^{ch}_{EV} \cdot P^{ch,EV}_{n,t} - \frac{P^{V2G}_{n,t}}{\eta^{dis}_{EV}}
\tag{EV-1}
\]

where:

- \(E^{EV}_{n,t}\) is the aggregated fleet SOC at node \(n\) and time step \(t\) (MWh)
- \(\eta^{ch}_{EV}\) is the fleet-average charging efficiency
- \(\eta^{dis}_{EV}\) is the fleet-average V2G discharging efficiency
- The SOC variable is indexed from \(t = 1\) (initial condition) to \(t = T+1\) (end of horizon)

The initial SOC is set to a configured fraction of total fleet battery capacity:

\[
E^{EV}_{n,1} = SOC^{init}_{EV} \cdot E^{total}_{EV,n}
\]

### EV-2: Charging Requirement (Driving Demand Satisfaction)

The EV fleet must receive sufficient charging energy to satisfy driving energy consumption. A slack variable allows partial unmet demand when grid conditions are extremely constrained:

\[
P^{ch,EV}_{n,t} + L^{EV}_{n,t} \geq D^{drive}_{n,t}
\tag{EV-2}
\]

where:

- \(D^{drive}_{n,t}\) is the driving consumption profile (MW), representing the energy required for mobility
- \(L^{EV}_{n,t} \geq 0\) is the EV charging loss/slack variable, penalized heavily in the objective to ensure driving demand is met whenever possible

This constraint ensures that the optimizer schedules at least enough charging to cover driving needs, while the slack variable provides a safety valve to maintain model feasibility during extreme scarcity events.

### EV-3: Charging Power Limit

The aggregate fleet charging power is bounded by the sum of individual vehicle charging capacities, with a feasibility floor:

\[
P^{ch,EV}_{n,t} \leq \max\left( \frac{\bar{P}^{ch}_{EV,kW} \cdot N_{EV,n}}{1000},\; 2 \cdot D^{drive}_{n,t} \right) \quad \forall n, t
\tag{EV-3}
\]

The floor of \(2 \times D^{drive}_{n,t}\) ensures that the charging limit is never below the driving consumption, which would make the problem infeasible. The factor of 2 provides headroom for the optimizer to schedule catch-up charging.

### EV-4: V2G Discharge Limit

V2G discharge is bounded by the aggregate fleet discharge capacity, modulated by the fleet availability profile:

\[
P^{V2G}_{n,t} \leq \frac{\bar{P}^{dis}_{EV,kW} \cdot N_{EV,n}}{1000} \cdot \alpha^{V2G}_{n,t} \quad \forall n, t
\tag{EV-4}
\]

where:

- \(\bar{P}^{dis}_{EV,kW}\) is the per-vehicle maximum V2G discharge power (kW)
- \(\alpha^{V2G}_{n,t} \in [0,1]\) is the V2G availability profile, representing the fraction of the fleet connected to the grid and available for V2G at each hour

The availability profile captures the fact that vehicles in transit or parked without grid connection cannot provide V2G services. Typical profiles show low availability during commute hours and high availability during nighttime and weekends.

### EV-5: SOC Bounds

The fleet SOC is bounded by configurable minimum and maximum fractions of total battery capacity:

\[
\underline{SOC}_{EV} \cdot E^{total}_{EV,n} \leq E^{EV}_{n,t+1} \leq \overline{SOC}_{EV} \cdot E^{total}_{EV,n} \quad \forall n, t
\tag{EV-5}
\]

where:

- \(\underline{SOC}_{EV}\) is the minimum SOC fraction (e.g., 0.20), preserving battery health and ensuring minimum driving range
- \(\overline{SOC}_{EV}\) is the maximum SOC fraction (e.g., 0.90), reflecting practical charging limits
- \(E^{total}_{EV,n} = N_{EV,n} \cdot E^{bat}_{EV} / 1000\) is the total fleet battery capacity in MWh

The SOC bounds protect battery longevity by avoiding deep discharge and overcharging, consistent with manufacturer recommendations and battery degradation science.

### EV-6: Charge/V2G Mutual Exclusivity

Simultaneous charging and V2G discharge at the same node is prevented:

\[
P^{ch,EV}_{n,t} \leq M_{EV} \cdot z^{EV}_{n,t}
\tag{EV-6a}
\]

\[
P^{V2G}_{n,t} \leq M_{EV} \cdot (1 - z^{EV}_{n,t})
\tag{EV-6b}
\]

where:

- \(z^{EV}_{n,t} \in [0,1]\) is the charging status variable (relaxed from binary for LP compatibility)
- \(M_{EV} = \bar{P}^{ch,total}_n + \bar{P}^{dis,total}_n\) is a big-M constant

The relaxation from binary to continuous is valid because the objective function naturally drives the optimizer to avoid simultaneous charge and discharge (which would waste energy through round-trip losses). In practice, the relaxed variable takes values close to 0 or 1.

---


## 5. Power System Integration


EV charging and V2G dispatch are fully integrated into the power balance equations of the operational dispatch model:

- **Charging** \(P^{ch,EV}_{n,t}\): appears as additional electrical demand on the right-hand side of the power balance
- **V2G** \(P^{V2G}_{n,t}\): appears as generation (supply) on the left-hand side of the power balance

In the single-bus power balance (PB-1):

\[
\sum_g P_{g,n,t} + \sum_b P^{dis}_{b,n,t} + P^{V2G}_{n,t} + LS_{n,t} + \ldots = D_{n,t} + \sum_b P^{ch}_{b,n,t} + P^{ch,EV}_{n,t} + \ldots
\]

In the multi-bus DC power flow balance (DC-1), V2G and EV charging appear at the respective bus with the same sign conventions.

---


## 6. Objective Terms


The EV model contributes two terms to the operational dispatch objective function:

### 6.1 V2G Compensation (Benefit)


V2G dispatch earns revenue at the time-varying electricity price, creating an incentive for the model to use V2G when it reduces overall system cost:

\[
Z^{V2G} = - \sum_{n,t} \pi_t \cdot P^{V2G}_{n,t} \tag{EV-OBJ-1}
\]

The negative sign indicates this is subtracted from total cost (i.e., it is a benefit). The electricity price \(\pi_t\) is derived from the dual of the power balance constraint.

### 6.2 EV Charging Loss Penalty


Unmet EV charging demand is penalized to ensure driving requirements are satisfied:

\[
Z^{EV,loss} = \sum_{n,t} c^{EV,loss} \cdot L^{EV}_{n,t} \tag{EV-OBJ-2}
\]

where \(c^{EV,loss}\) is the EV loss penalty coefficient (`ev_config.loss_penalty`). This should be set high enough to ensure the optimizer meets driving demand under normal conditions, but below VOLL to allow shedding EV demand before critical loads.

### 6.3 Penalty Hierarchy


The penalty structure establishes a rational shedding order:

\[
c^{VOLL} \gg c^{EV,loss} > c^{curtailment}
\]

This ensures that when the system is capacity-constrained, the optimizer first curtails renewable generation, then sheds EV charging demand, and only as a last resort sheds critical electrical loads.

---


## 7. Interaction with Capacity Expansion


In the Master Problem, the EV fleet parameters are projected forward using the S-curve adoption model. The fleet size \(N_{EV,n}(y)\) at each node and year determines:

1. The peak EV charging demand that must be satisfied, which influences capacity adequacy requirements
2. The available V2G capacity, which can offset investment in stationary storage
3. The total energy throughput requirements, which influence fuel supply planning

The Master Problem does not directly optimize EV charging schedules; instead, it uses representative day subproblems with projected fleet parameters to validate that investment plans are feasible given the growing EV fleet.

---


## 8. Configuration Reference


```yaml
ev_config:
  enabled: true
  loss_penalty: 500.0             # $/MW penalty for unmet EV demand

  ev_categories:
    sedan:
      battery_capacity_kwh: 60.0
      max_charge_power_kw: 11.0
      max_discharge_power_kw: 7.0
      charge_efficiency: 0.95
      discharge_efficiency: 0.92
      min_soc: 0.20
      max_soc: 0.90
      v2g_compensation: 0.05

  ev_quantity:
    sedan: [1000, 1200, 1500]

  growth_rate: 0.3
  max_adoption: 10.0
  mid_point_fraction: 0.5

  base_patterns:
    commuter: [0.1, 0.1, ...]    # 24 hourly values

  v2g_availability:
    weekday: [0.8, 0.8, 0.8, 0.8, 0.8, 0.6, 0.3, 0.1, 0.1, 0.1, 0.1, 0.1,
              0.1, 0.1, 0.1, 0.2, 0.3, 0.5, 0.7, 0.8, 0.8, 0.8, 0.8, 0.8]
```

---


## 9. EV Adoption Modeling (Wizard)

The EV & V2G Assessment Wizard (`models/ev_adoption.py`) provides four data-driven methods to *populate* the fleet parameters in sections 1-3 above, bridging real-world transport data to the optimizer inputs:

### 9.1 Method Summary

| Method | Model | Key Drivers |
|--------|-------|-------------|
| **Logistic Regression** | \(\text{penetration} = \sigma(\beta_0 + \beta_{fuel} \cdot \Delta_{fuel} + \beta_{GDP} \cdot GDP + \ldots)\) | Fuel savings, EV cost, charging infrastructure, GDP, urbanization |
| **Bass Diffusion** | \(F(t) = \frac{1 - e^{-(p+q)t}}{1 + (q/p) \cdot e^{-(p+q)t}}\) | Innovation coefficient \(p\), imitation coefficient \(q\) |
| **TCO-Parity** | \(\text{adoption} = \sigma(s \cdot (TCO_{ICE} - TCO_{EV}) / TCO_{ICE})\) | Battery learning curve, fuel prices, subsidies, vehicle lifetime |
| **Policy-Driven** | Linear sales ramp + scrappage model | ICE ban year, emission targets, vehicle lifetime |

All methods produce uniform `EVAdoptionCurve` outputs (year-by-year penetration, fleet by category, energy demand, peak charging) that feed into `fit_adoption_to_ev_config()`.

### 9.2 S-Curve Parameter Fitting

The integration function `fit_adoption_to_ev_config()` uses least-squares fitting (`scipy.optimize.curve_fit`) to map any adoption trajectory to the logistic parameters \(K\), \(r\), \(y_{mid}\) from equation (EV-S), producing the `max_adoption`, `growth_rate`, and `mid_point_fraction` fields required by the optimizer.

### 9.3 V2G Technical Potential

Module `models/ev_analysis.py` provides:

- **Charging demand characterization**: Three scenarios (uncontrolled, TOU-shifted, optimized valley-filling)
- **V2G capacity**: Hourly discharge potential from connected-time profiles and participation rates
- **Battery degradation**: Wohler-type cycle aging + calendar aging for NMC/LFP chemistries, yielding break-even V2G compensation rates
- **Grid impact assessment**: Peak shaving, valley filling, arbitrage revenue, avoided grid reinforcement

See [API: Models EV Adoption](../api/models-ev-adoption.md) and [API: Models EV Analysis](../api/models-ev-analysis.md) for full function reference.

---


## 10. Implementation Reference


| Function | Language | File | Purpose |
|----------|----------|------|---------|
| `generate_ev_profiles()` | Python | `models/ev.py` | S-curve fleet growth and profile generation |
| `run_ev_logistic_adoption()` | Python | `models/ev_adoption.py` | Logistic regression adoption model |
| `run_ev_bass_diffusion()` | Python | `models/ev_adoption.py` | Bass diffusion adoption model |
| `run_ev_tco_parity()` | Python | `models/ev_adoption.py` | TCO-parity adoption model |
| `run_ev_policy_driven()` | Python | `models/ev_adoption.py` | Policy-driven adoption model |
| `fit_adoption_to_ev_config()` | Python | `models/ev_adoption.py` | S-curve fitting for ESFEX config |
| `generate_charging_profiles()` | Python | `models/ev_analysis.py` | 24h charging demand profiles |
| `compute_v2g_potential()` | Python | `models/ev_analysis.py` | V2G capacity assessment |
| `compute_battery_degradation()` | Python | `models/ev_analysis.py` | Wohler degradation model |
| `assess_grid_impact()` | Python | `models/ev_analysis.py` | Grid impact analysis |
| `add_ev_constraints!()` | Julia | `power_system.jl` | EV-1 through EV-6 constraints |
| `build_objective!()` | Julia | `power_system.jl` | V2G compensation and EV loss penalty terms |
| `extract_solution()` | Julia | `power_system.jl` | EV charging, V2G, and SOC time series extraction |

---

## References

The vehicle-to-grid (V2G) concept and capacity calculation follow Kempton and Tomić [**[51]**](../reference/bibliography.md#ref51). EV integration impacts on power systems are reviewed by Lopes et al. [**[52]**](../reference/bibliography.md#ref52). The Bass diffusion model for technology adoption follows Bass [**[53]**](../reference/bibliography.md#ref53). Battery degradation from cycling (Wohler model) draws on Xu et al. [**[32]**](../reference/bibliography.md#ref32).

See the [full bibliography](../reference/bibliography.md) for complete citation details.
