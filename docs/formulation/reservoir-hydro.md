# Reservoir Hydropower

The reservoir hydropower model gives hydroelectric generators an explicit water-energy budget instead of treating them as firm capacity. It is implemented in `add_reservoir_constraints!` (`power_system.jl`) and is shared by both the operational dispatch and the capacity-expansion master problem.

---


## 1. Overview


A generator becomes a reservoir unit when `reservoir_capacity` is non-zero at one or more nodes. Its energy is then tracked as a stored "bucket" (MWh-equivalent): generation draws the reservoir down, inflow and pumping fill it, and spillage and evaporation are losses. Reservoir hydro is therefore **energy-limited**, not firm — a key correction over treating must-run renewables as always-available capacity.

The model supports five behaviours, each independently optional:

1. **Water-energy budget** — the core balance, always active for a reservoir unit.
2. **Minimum environmental flow** — a mandatory ecological release floor.
3. **Seasonal storage** — water carried chronologically across representative periods (via TSAM inter-period linking).
4. **Hydraulic cascade** — an upstream unit's release feeds a downstream reservoir.
5. **Head dependence** — a depleted reservoir delivers less peak power.

All constraints are linear, so the model remains an LP (no integers, no bilinear terms).

---


## 2. Decision Variables

Per reservoir generator \(g\), node \(b\), and hour \(t\):

| Variable | Domain | Units | Description | Julia name |
|----------|--------|-------|-------------|------------|
| \(L_{g,b,t}\) | \(\mathbb{R}_+\) | MWh-eq | Reservoir storage level, \(t = 1\ldots H{+}1\) | `reservoir_level[g,b,t]` |
| \(P_{g,b,t}\) | \(\mathbb{R}_+\) | MW | Turbined generation | `gen_output[g,b,t]` |
| \(S_{g,b,t}\) | \(\mathbb{R}_+\) | MW-eq | Spillage (release without generation) | `reservoir_spillage[g,b,t]` |
| \(U_{g,b,t}\) | \(\mathbb{R}_+\) | MW | Pump-back power (charging the reservoir) | `reservoir_pump[g,b,t]` |

Parameters: reservoir capacity \(C\), turbine efficiency \(\eta\), pump efficiency \(\eta_p\), evaporation rate \(\rho\), hourly inflow \(W_t\), minimum/maximum level fractions \(\ell^{\min},\ell^{\max}\), initial fraction \(\ell^0\), minimum release \(R^{\min}\), head-min factor \(\phi\), cascade delay \(\tau\), and rated power \(\bar P\).

---


## 3. Constraints

### RES-1: Water balance

The reservoir level evolves by inflow (natural + cascade), turbining, pumping, spillage and evaporation:

$$
L_{g,b,t+1} = L_{g,b,t}\,(1-\rho) + W_{g,b,t} + \Phi_{g,b,t} - \frac{P_{g,b,t}}{\eta} + \eta_p\,U_{g,b,t} - S_{g,b,t}
$$

where \(\Phi_{g,b,t}\) is the cascade inflow (RES-5, zero for a unit with no upstream feeders).

### RES-2: Level band

$$
\ell^{\min} C \le L_{g,b,t+1} \le \ell^{\max} C
$$

Pumping is bounded by the pump capacity, and spillage is forced to zero when `reservoir_spillage_allowed` is false.

### RES-3: Boundary condition

The starting level is fixed to the configured fraction, and the period is **cyclic** (the end level returns to the start, within `soc_end_tolerance`):

$$
L_{g,b,1} = \ell^0 C, \qquad L_{g,b,H+1} \approx L_{g,b,1}
$$

With **seasonal storage** enabled (see §4), the cyclic closure is replaced by a chronological chain: the level starts at the previous period's boundary and ends pinned to the next, so water is carried across periods rather than reset each one.

### RES-4: Minimum environmental flow

When `reservoir_min_release` \(R^{\min} > 0\), the water leaving the reservoir (turbined and/or spilled) must meet the mandatory downstream flow:

$$
\frac{P_{g,b,t}}{\eta} + S_{g,b,t} \ge R^{\min}
$$

### RES-5: Hydraulic cascade

Let \(\mathcal{U}(g)\) be the reservoirs that discharge into \(g\), each with travel delay \(\tau_u\). The cascade inflow injected into \(g\)'s primary node is the upstream release (turbined + spilled), shifted by the delay:

$$
\Phi_{g,b,t} = \sum_{u \in \mathcal{U}(g)} \left( \frac{P_{u,t-\tau_u}}{\eta_u} + S_{u,t-\tau_u} \right), \qquad t - \tau_u \ge 1
$$

Releases scheduled before the period start (\(t - \tau_u < 1\)) are outside the window and not carried in.

### RES-6: Head-dependent power limit

When `reservoir_head_min_factor` \(\phi < 1\), a low reservoir has low head and cannot reach nameplate power. The available output scales linearly with the fill level, from \(\phi\,\bar P\) at the minimum level to \(\bar P\) at the maximum:

$$
P_{g,b,t} \le \bar P\left( \phi + (1-\phi)\,\frac{L_{g,b,t} - \ell^{\min} C}{(\ell^{\max} - \ell^{\min})\,C} \right)
$$

Evaluated at the start-of-step level \(L_{g,b,t}\), this bound is linear in the level variable — the tractable piecewise-linear treatment of head dependence. Reservoir investment is not folded into the fill fraction, keeping the constraint linear.

---


## 4. Master-Problem Integration

In the capacity-expansion master problem the same balance (RES-1–RES-6) is enforced inside every representative-day subproblem, so investment decisions see hydro as an energy-limited resource rather than firm MW.

**Seasonal storage.** When `tsam_inter_period_linking` is enabled (TSAM time aggregation), the master builds a chronological chain of reservoir-level boundary variables — one per reservoir node, year-cyclic — mirroring the battery state-of-charge chain. Each representative period starts at the previous boundary level and ends at the next, so a reservoir can be filled in a wet period and drawn down in a later dry one. This captures genuinely seasonal hydro storage that a per-period cyclic model cannot represent.

---


## 5. Configuration

See [GeneratorConfig — reservoir fields](../reference/config-reference.md#generatorconfig) for the YAML schema and the [generator element form](../gui/element-forms.md#reservoir-mode-hydro-generators) for the Studio GUI fields. The seasonal-storage toggle lives in [Global Settings](../gui/global-settings.md).
