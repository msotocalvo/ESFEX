# Constraint Catalog

## power_system.jl --- Operational Dispatch

### Generator Constraints

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `gen_zero_cap_g{g}_b{b}_t{t}` | GEN-0 | Zero output for inactive generators (`rated=0` and no investment) |
| `gen_output <= rated * availability * status` | GEN-1 | Upper bound on generation |
| `gen_output >= min_power * status` | GEN-2 | Minimum stable generation |
| `gen_output[t] - gen_output[t-1] <= ramp_up` | GEN-3 | Ramp-up rate limit |
| `gen_output[t-1] - gen_output[t] <= ramp_down` | GEN-4 | Ramp-down rate limit |
| `startup_detect_g{g}_b{b}_t{t}` | GEN-5a | Startup detection: `startup >= status[t] - status[t-1]` |
| `min_up_g{g}_b{b}_t{t}_tau{t}` | GEN-5b | Minimum up time |
| `min_down_g{g}_b{b}_t{t}_tau{t}` | GEN-5c | Minimum down time |
| `gen_delay_ret_{g}_{b}` | GEN-RET | Delay retirement penalty |
| `gen_forced_repl_{g}_{b}` | GEN-REPL | Forced replacement penalty |

### PWL Cost Curve Constraints (Generators)

Generators with multi-segment (PWL) cost curves have their output decomposed into segment variables linked to the aggregate output.

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `gen_output[g,b,t] == sum(gseg_{g}_{b}[k,t] for k)` | PWL-G1 | Segment output summation: aggregate output equals sum of all segment outputs |
| `gseg_{g}_{b}[k,t] <= fraction_k * rated * availability` | PWL-G2 | Segment upper bound (renewable): each segment limited to its fraction of available capacity |
| `gseg_{g}_{b}[k,t] <= fraction_k * total_capacity` | PWL-G3 | Segment upper bound (non-renewable): each segment limited to its fraction of total capacity |

The objective applies per-segment marginal costs: `cost += marginal_cost_k * gseg[k,t]`, replacing the flat `fuel_cost * gen_output` term.

### Reservoir Constraints

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `reservoir_level[g,n,1] == initial_level * capacity` | RES-1 | Initial reservoir level |
| `level[t+1] = level[t]*(1-evap) + inflow - output/eta_t + pump*eta_p - spillage` | RES-2 | Reservoir dynamics (water balance) |
| `min_level * total_cap <= level[g,n,t] <= max_level * total_cap` | RES-3 | Reservoir level bounds |
| `pump[g,n,t] <= pump_capacity[n]` | RES-4 | Pump-back power limit |
| `spillage[g,n,t] <= capacity` (or 0 if not allowed) | RES-5 | Spillage limit |
| `level[g,n,end] ~ initial_level * capacity` (within tolerance) | RES-6 | Cyclic end-of-horizon constraint |
| `reservoir_pump_term` subtracted from power balance | RES-PB | Pump as demand-side load |

### Battery Constraints

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `bat_zero_charge_b{bi}_bus{b}_t{t}` | BAT-0a | Zero charge for inactive batteries |
| `bat_zero_discharge_b{bi}_bus{b}_t{t}` | BAT-0b | Zero discharge for inactive batteries |
| `bat_zero_soc_b{bi}_bus{b}_t{t}` | BAT-0c | Zero SOC for inactive batteries |
| `charge <= MaxChargePower` | BAT-1 | Maximum charge rate |
| `discharge <= MaxDischargePower` | BAT-2 | Maximum discharge rate |
| `soc[t] = soc[t-1] + eta_c*charge - discharge/eta_d - self_discharge*soc[t-1]` | BAT-3 | SOC dynamics |
| `soc >= max_DoD * capacity` | BAT-4 | Minimum SOC (max depth of discharge) |
| `soc <= capacity` | BAT-5 | Maximum SOC |
| `bat_soc_initial_{bi}_{b}` | BAT-6 | Initial SOC from boundary conditions |
| `bat_soc_end_lower_{bi}_{b}` | BAT-7a | End-of-horizon SOC lower bound |
| `bat_soc_end_upper_{bi}_{b}` | BAT-7b | End-of-horizon SOC upper bound |
| `bat_min_cycling_{bi}_{b}` | BAT-8 | Minimum cycling requirement over period |
| `bat_spillage_{bi}_{b}_{t}` | BAT-9 | Spillage limit |
| `bat_delay_ret_{bi}_{b}` | BAT-RET | Battery delay retirement penalty |
| `bat_forced_repl_{bi}_{b}` | BAT-REPL | Battery forced replacement penalty |

### PWL Cost Curve Constraints (Batteries)

Batteries with multi-segment discharge cost curves have discharge power decomposed into segment variables analogously to generators.

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `bat_discharge[bi,b,t] == sum(bseg_{bi}_{b}[k,t] for k)` | PWL-B1 | Segment discharge summation: aggregate discharge equals sum of segment outputs |
| `bseg_{bi}_{b}[k,t] <= fraction_k * MaxDischargePower` | PWL-B2 | Segment upper bound: each discharge segment limited to its fraction of max discharge power |

The objective applies per-segment marginal costs: `cost += marginal_cost_k * bseg[k,t]`, replacing the flat throughput degradation cost term.

### Reserve Constraints

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `reserve_static_{b}_{t}` | RES-1 | Static reserve: `reserve_supply + loss_of_reserve >= requirement` |
| `reserve_dynamic_avail_{b}_{t}` | RES-2a | Dynamic reserve availability |
| `reserve_dynamic_req_{b}_{t}` | RES-2b | Dynamic reserve requirement |

### Power Balance

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| Single-bus: `sum(gen) - sum(charge) + sum(discharge) + loss_load = demand` | PB-1 | Power balance (single node) |
| Multi-bus via DC power flow (see `transmission_dc.jl`) | PB-2 | Network power balance |

### N-1 Security

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `n1_gen_reserve_system_t{t}` | N1-1 | Generation N-1: reserve >= largest unit |
| `n1_trans_reserve_{i}_{j}_t{t}_pos` | N1-2a | Transmission N-1 positive direction |
| `n1_trans_reserve_{i}_{j}_t{t}_neg` | N1-2b | Transmission N-1 negative direction |

### Curtailment

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `curtailment[g,b,t] <= gen_output[g,b,t]` (renewables only) | CUR-1 | Curtailment definition |
| `sum(curtailment) <= max_curtailment_ratio * sum(renewable_gen)` | CUR-2 | System curtailment limit |
| `rooftop_curt_limit_b{b}_t{t}` | CUR-3 | Rooftop curtailment limit |

### Renewable Energy Target

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `re_penetration_target` | RE-1 | `sum(renewable_gen) / sum(total_gen) >= target - loss` |

### CO2 Emissions

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `co2_emissions_def_b{b}_t{t}` | CO2-1 | Emissions definition per bus/time |
| `CO2_budget_constraint` | CO2-2 | `sum(emissions) <= annual_budget + violation` |

### Inertia

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `inertia_{t}` | INE-1 | `sum(inertia * status * rated) + loss_inertia >= threshold` |

### EV Constraints

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `ev_soc_initial_{b}` | EV-1 | Initial EV fleet SOC |
| `ev_soc_dynamics_{b}_{t}` | EV-2 | SOC dynamics: `soc[t] = soc[t-1] + eta_c*charge - v2g/eta_d` |
| `ev_demand_{b}_{t}` | EV-3 | EV charging demand requirement |
| `ev_max_charge_{b}_{t}` | EV-4 | Max EV charging power |
| `ev_max_v2g_{b}_{t}` | EV-5 | Max V2G discharge power |
| `ev_soc_min_{b}_{t}` | EV-6a | Minimum EV SOC |
| `ev_soc_max_{b}_{t}` | EV-6b | Maximum EV SOC |
| `ev_mutex_charge_{b}_{t}` | EV-7a | Charge/discharge mutex (charge) |
| `ev_mutex_v2g_{b}_{t}` | EV-7b | Charge/discharge mutex (V2G) |

### Demand & Sectoral

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `max_load_shed_b{b}_t{t}` | SEC-1 | Maximum load shedding at bus |
| `sectoral_lol_sum_{b}_{t}` | SEC-2 | Sectoral LOL aggregation |
| `sectoral_lol_cap_{sector}_{b}_{t}` | SEC-3 | Per-sector LOL capacity |
| `flex_curt_cap_{sector}_{b}_{t}` | SHIFT-1a | Flexible demand curtailment cap |
| `demand_shift_out_cap_{sector}_{b}_{t}` | SHIFT-1b | Demand shift-out capacity |

### Investment & Transfer

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `max_node_inv_{b}` | INV-1 | Max investment per node |
| `max_annual_system_cost` | INV-2 | Annual system cost cap |
| `transfer_margin_{i}_{j}` | TRN-1 | Transfer margin constraint |

---

## transmission_dc.jl --- DC Power Flow

### Network Balance (KCL)

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `net_inj == flow_sum` | DC-1 | Kirchhoff's Current Law at each bus |
| `voltage_drop == 0` | DC-1s | Single-node voltage (no network) |

### PWL Transmission Losses

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `pf == fpos - fneg` | LOSS-1 | Flow direction decomposition |
| `fpos == sum_k(dp_k)` | LOSS-2a | Positive direction segment sum |
| `fneg == sum_k(dn_k)` | LOSS-2b | Negative direction segment sum |
| `dp_k <= delta_f_k` | LOSS-3a | Segment width bound (positive) |
| `dn_k <= delta_f_k` | LOSS-3b | Segment width bound (negative) |
| `ploss == sum_k(m_k * (dp_k + dn_k))` | LOSS-4 | PWL loss computation |
| `net_inj == sum_l(K * pf - 0.5 * abs(K) * ploss)` | LOSS-5 | KCL with half-loss split |

### Kirchhoff's Voltage Law (KVL)

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `angle[from] - angle[to] == reactance * flow` | DC-2 | Voltage angle / power flow relation |
| `voltage_angle[slack] == 0` | DC-3 | Slack bus reference angle |

### Line Capacity

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `flow[i,j,t] <= capacity + investment` | DC-4a | Line capacity (positive direction) |
| `flow[i,j,t] >= -(capacity + investment)` | DC-4b | Line capacity (negative direction) |
| `investment[i,j] == investment[j,i]` | DC-5 | Bidirectional investment symmetry |
| `angle_diff <= max_angle_deg` | DC-6 | Voltage angle difference limit |

### Devices

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `rectify + invert <= rated_power` | CONV-1 | AC/DC converter power limit |
| `freq_ab + freq_ba <= rated_power` | CONV-2 | Frequency converter power limit |

### Load Shedding (DC mode)

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `loss_load[b,t] <= demand[b,t] * threshold` | DC-LS | Load shedding limit (if threshold < 1.0) |

---

## master_problem.jl --- Capacity Expansion

### Investment Variables & Constraints

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| Cumulative gen investment limit | INV-1 | `sum_y(invest[g,n,y]) <= invest_max[g,n]` |
| Cumulative battery power limit | INV-2 | `sum_y(invest_pow[b,n,y]) <= invest_max_power[b,n]` |
| Cumulative battery capacity limit | INV-3 | `sum_y(invest_cap[b,n,y]) <= invest_max_capacity[b,n]` |
| Cumulative reservoir capacity limit | INV-3r | `sum_y(reservoir_invest[g,n,y]) <= reservoir_invest_max[g,n]` |
| `min_duration_{bi}_{b}` | INV-4a | Battery min E/P ratio |
| `max_duration_{bi}_{b}` | INV-4b | Battery max E/P ratio |

### Technology Investment Constraints

When per-technology investment is enabled (via `technologies` and `battery_technologies`), the master problem creates technology-level investment variables instead of per-generator variables.

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| Cumulative technology gen investment limit | TECH-1 | `sum_y(tech_invest[t,n,y]) <= tech_invest_max[t,n]` |
| Cumulative battery tech power limit | TECH-2 | `sum_y(btech_invest_pow[t,n,y]) <= btech_invest_max_power[t,n]` |
| Cumulative battery tech capacity limit | TECH-3 | `sum_y(btech_invest_cap[t,n,y]) <= btech_invest_max_capacity[t,n]` |
| Battery tech min E/P ratio | TECH-4a | `btech_invest_cap[t,n,y] >= min_duration * btech_invest_pow[t,n,y]` |
| Battery tech max E/P ratio | TECH-4b | `btech_invest_cap[t,n,y] <= max_duration * btech_invest_pow[t,n,y]` |

### Budget

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `annual_cost <= max_annual_investment + slack` | BUD-1 | Annual investment budget with slack for feasibility |

### Capacity Adequacy

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `capacity_adequacy_{y_idx}_{n}` | CAP-1 | Total capacity >= peak_demand * reserve_margin |

### RE Targets

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `re_penetration_target_{y_idx}` | RE-1 | Annual RE penetration target |
| `re_penetration_min_increment` | RE-2 | Minimum annual RE growth |
| `re_penetration_max_increment` | RE-3 | Maximum annual RE growth |

### Retirement

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| Age-based: `age_at_year >= lifetime -> capacity = 0` | RET-1 | Existing unit retirement |
| Investment: `year - invest_year >= lifetime -> capacity = 0` | RET-2 | Invested unit retirement |

### Operational Validation (Representative Days)

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| Generator output bounds per day | OP-1 | `gen[g,n,t] <= available_capacity` |
| Battery SOC dynamics per day | OP-2 | SOC balance with cyclic constraint |
| Power balance per day | OP-3 | Supply meets demand |
| Curtailment limit per day | OP-4 | `curtailment <= ratio * renewable` |
| `sectoral_lol_cap_{sector}_{n}_{t}` | OP-5 | Sectoral LOL per rep. day |

### Inter-System Transmission (Multi-System DC-OPF)

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `pf = fp - fn` | IS-1 | Bidirectional flow decomposition |
| `pf <= cap_base + sum(invest)` | IS-2 | Capacity limit (positive direction) |
| `pf >= -(cap_base + sum(invest))` | IS-3 | Capacity limit (negative direction) |
| `ploss = sum_k(m_k * (dp_k + dn_k))` | IS-4 | PWL loss approximation (or linear fallback) |
| `ext_inj_FROM = +pf - 0.5*ploss` | IS-5 | KCL injection at FROM bus (half-loss split) |
| `ext_inj_TO = -pf - 0.5*ploss` | IS-6 | KCL injection at TO bus (half-loss split) |
| Link investment limit | MS-1 | `sum_y(link_invest[l,y]) <= max_investment` |
| Border injection | MS-3 | Link power enters/exits at border nodes |

---

## electrolyzer.jl --- Electrolysis

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `power <= total_capacity` | ELZ-1 | Electrolyzer power limit |
| `h2_prod == power * 1000 * eff / energy_per_kg` | ELZ-2 | H2 production formula |
| `power[t] - power[t-1] <= ramp_up` | ELZ-3a | Ramp-up limit |
| `power[t-1] - power[t] <= ramp_down` | ELZ-3b | Ramp-down limit |

---

## primary_energy.jl --- Fuel Supply Chain

### Supply & Transport

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `supply[f,n,p] <= max_availability[f,n]` | PE-1 | Fuel supply limit |
| `transport[f,n,m,p] <= capacity + investment` | PE-2 | Transport capacity |
| `received = sent * (1 - loss_rate * distance)` | PE-3 | Transport losses |

### Storage

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `level[f,n,p] = level[p-1] + supply - consumption - transport_out` | PE-4 | Storage balance |
| `min_level * capacity <= level <= capacity + investment` | PE-5 | Storage bounds |
| `level[final] == level[initial]` | PE-6 | Cyclic storage constraint |

### Demand

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `non_electric_consumption + loss >= demand` | PE-7 | Non-electric demand satisfaction |
| `gen_consumption = gen_output / efficiency * energy_content` | PE-8 | Generator fuel linkage |

### Emissions

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `emissions = consumption * emission_factor` | PE-EM | Emission calculation |

---

## mga.jl --- MGA and SPORES

### Cost Slack Constraint

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `total_cost <= (1 + slack) * optimal_cost` | MGA-1 | Near-optimal cost slack: total system cost must be within `slack_fraction` of the cost-optimal objective. Shared by both methods |

### MGA — Classical Hop-Skip-Jump diversity objective

The HSJ objective replaces the cost objective after the cost-optimal solution is found. Frequency-based scoring assigns scores based on how often each investment variable has been selected in previous alternatives.

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `max sum(score_k * x_k / x_max_k)` | MGA-2 | Maximize weighted diversity: `score = 1 - 2 * frequency`, where frequency is the fraction of previous alternatives that invested in variable `k`. Used by `run_mga_spores` and reusable inside a SPORES sweep when `:hsj_diversity` is in the objective list |

### SPORES — Per-objective sweep

Each SPORES objective replaces the cost objective (and any previous SPORES objective's aux vars / constraints, via `_clear_spores_aux!`) under the same cost-slack envelope (MGA-1). All formulations are LP — the L1 distance in SPORES-4 is linearised with positive / negative deviation aux variables.

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `min sum(I)` | SPORES-1 | Minimum total build: $\min \sum_{y,t,n} I^{tech} + \sum_{y,b,n} I^{bat,P} + \sum_{y,(i,j)} I^{tr}$. No auxiliary variables. Implemented by `set_min_build_objective!` |
| `min M, sum(I_t/I_max) <= M ∀ t` | SPORES-2 | Technology equity (min-max over per-tech totals). Adds 1 auxiliary scalar $M$ and $|\mathcal T|$ constraints. Implemented by `set_tech_equity_objective!` |
| `min M, sum(I_n/I_max) <= M ∀ n` | SPORES-3 | Regional equity (min-max over per-node totals). Adds 1 auxiliary scalar $M$ and $|\mathcal N|$ constraints. The spatially-explicit objective from which SPORES gets its name. Implemented by `set_regional_equity_objective!` |
| `I - I_ref = d_pos - d_neg; max sum((d_pos + d_neg)/I_max)` | SPORES-4 | Evolutionary distance (L1 from a reference solution, typically the cost-optimal). Adds 2 auxiliary variables and 1 constraint per investment variable. Implemented by `set_evolutionary_distance_objective!` |

---

## transmission_acopf.jl --- AC Optimal Power Flow

### Voltage Constraints

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `soc_{l}_{t}` | AC-SOC | SOC relaxation: `w_i × w_j >= wr² + wi²` per branch |
| `qc_wr_lb_{l}_{t}`, `qc_wr_ub_{l}_{t}` | AC-QC1 | QC tighter bounds on wr using cos bounds |
| `qc_wi_lb_{l}_{t}`, `qc_wi_ub_{l}_{t}` | AC-QC2 | QC tighter bounds on wi using sin bounds |
| `qc_cos_env_{l}_{t}` | AC-QC3 | QC convex envelope for cos relaxation |
| `qc_angle_ub_{l}_{t}`, `qc_angle_lb_{l}_{t}` | AC-QC4 | QC angle bounds via tan(θ_max) |
| `angle_ub_{l}_{t}`, `angle_lb_{l}_{t}` | AC-ANG | Polar/Rect angle difference limits |
| `vm_lb_{i}_{t}`, `vm_ub_{i}_{t}` | AC-VM | Rectangular voltage magnitude bounds: `v_min² ≤ e² + f² ≤ v_max²` |

### Power Balance

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `kcl_p_{i}_{t}` | AC-P | Active power balance: `net_injection_MW = base_mva × Σ P_flow_pu` |
| `kcl_q_{i}_{t}` | AC-Q | Reactive power balance: `Q_gen - Q_load + Q_slack = base_mva × Σ Q_flow_pu` |

### Line Limits

| Label Pattern | Family | Description |
|---------------|--------|-------------|
| `sline_from_{l}_{t}` | AC-SL1 | Apparent power from-side: `P_from² + Q_from² ≤ cap_pu²` |
| `sline_to_{l}_{t}` | AC-SL2 | Apparent power to-side: `P_to² + Q_to² ≤ cap_pu²` |

---

## Constraint Count Summary

| Module | Approximate Constraints | Variables |
|--------|----------------------|-----------|
| `power_system.jl` | ~37 families + PWL segments | gen_output, bat_charge, bat_discharge, bat_soc, reservoir_level, reservoir_pump, reservoir_spillage, loss_load, curtailment, ev_charge, ev_v2g, ev_soc, gen_seg_output, bat_seg_discharge |
| `master_problem.jl` | ~16 families + technology investment | gen_invest, bat_invest_power, bat_invest_capacity, reservoir_invest_capacity, transfer_invest, tech_invest, btech_invest_power, btech_invest_capacity |
| `transmission_dc.jl` | ~8 families | power_flow, voltage_angle, transfer_investment |
| `electrolyzer.jl` | ~4 families | elz_power, h2_production |
| `primary_energy.jl` | ~8 families | fuel_supply, fuel_transport, fuel_storage, fuel_consumption |
| `mga.jl` | ~6 families (MGA-1, MGA-2, SPORES-1..4) | reuses master-problem variables; SPORES-2/3 add 1 scalar each, SPORES-4 adds 2 per investment var |
| `transmission_acopf.jl` | ~7 families (SOC/QC/VM/ANG/KCL-P/KCL-Q/SL) | w, wr, wi, vm, va, vr, vi_rect, q_gen, q_slack_pos, q_slack_neg |

Total: **~82+ constraint families** across the optimization model.
