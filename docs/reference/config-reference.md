# Configuration Reference

## Top-Level: ESFEXConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `simulation_mode` | `"development"` \| `"unit_commitment"` | `"development"` | Simulation mode |
| `unit_commitment_hours` | int (>=1) | 24 | Hours per UC window |
| `date_start` | str | `"01/01/2025 00:00"` | Simulation start date |
| `temporal` | TemporalConfig | See below | Time resolution settings |
| `solver` | SolverConfig | See below | Solver configuration |
| `n1_security` | N1SecurityConfig | See below | Contingency analysis |
| `master_problem` | MasterProblemConfig | See below | Capacity expansion settings |
| `enable_primary_energy` | bool | `true` | Include fuel supply chain |
| `meta_network` | MetaNetworkConfig | See below | Multi-system configuration |
| `systems` | dict[str, SystemConfig] | Required | Named power systems |
| `plugins` | dict[str, Any] | `{}` | Per-plugin configuration keyed by plugin name |

---

## TemporalConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `resolution_hours` | int (>=1) | 1 | Operational time step (hours) |
| `rolling_horizon_hours` | int (>=1) | 48 | Rolling window size (hours) |
| `overlap_hours` | int (>=0) | 6 | Window overlap (hours) |
| `investment_resolution` | int | 8760 | Master problem time step (hours) |
| `primary_energy_resolution` | int | 24 | Fuel supply chain time step (hours) |
| `use_rolling_horizon` | bool | `true` | Enable rolling horizon dispatch |

---

## SolverConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `"highs"` \| `"cbc"` \| `"glpk"` \| `"gurobi"` \| `"cplex"` | `"highs"` | Solver backend |
| `threads` | int (>=1) | 4 | Parallel threads |
| `time_limit` | int (>=0) | 10800 | Time limit (seconds) |
| `gap` | float (0-1) | 0.01 | MIP optimality gap |
| `verbose` | bool | `false` | Solver output logging |
| `scale_constraints` | bool | `true` | Numerical scaling |
| `options` | dict | `{}` | Solver-specific options |

---

## N1SecurityConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable N-1 contingency analysis |
| `apply_to_modes` | list[str] | `["unit_commitment"]` | Active modes |
| `transmission_enabled` | bool | `true` | Test line outages |
| `transmission_reserve_factor` | float (0-1) | 0.70 | Post-contingency capacity |
| `critical_line_threshold` | float (>=0) | 0.0 | Min utilization to test |
| `generation_enabled` | bool | `true` | Test generator outages |
| `generation_reserve_type` | `"largest_unit"` \| `"percentage"` | `"largest_unit"` | Reserve sizing method |
| `generation_reserve_percentage` | float (0-1) | 0.15 | Capacity reserve fraction |

---

## MasterProblemConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `stochastic` | bool | `false` | Enable multi-scenario |
| `representative_days` | int (>=1) | 5 | Number of representative days (ignored when `use_tsam=true`) |
| `min_day_separation` | int (>=1) | 5 | Min hours between rep. days (ignored when `use_tsam=true`) |
| `use_tsam` | bool | `false` | Enable TSAM clustering for representative period selection |
| `tsam_num_periods` | int (2-365) | 10 | Number of representative periods for TSAM |
| `tsam_method` | `"kmedoids"` / `"kmeans"` | `"kmedoids"` | Clustering method |
| `tsam_inter_period_linking` | bool | `true` | Enable inter-period SOC linking for seasonal storage |
| `mga` | MGAConfig | See below | MGA/SPORES near-optimal exploration |

---

## MGAConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable MGA / SPORES alternative generation |
| `method` | `"mga"` \| `"spores"` | `"mga"` | Generation method. `"mga"` runs the Hop-Skip-Jump loop $K$ times. `"spores"` solves one alternative per entry in `objectives` |
| `objectives` | list[SporesObjective] | `[]` | SPORES objective menu (see [SporesObjective](#sporesobjective)). Required when `method = "spores"`, must be empty when `method = "mga"` |
| `num_alternatives` | int (1-100) | 10 | Number of diversity alternatives. **Used only when `method = "mga"`** — ignored for SPORES (the count equals `len(objectives)`) |
| `slack_fraction` | float (0.0-0.5) | 0.05 | Maximum cost increase above optimal (0.05 = 5%) — shared by both methods |
| `investment_threshold` | float (>=0) | 0.1 | MW threshold to count as "invested" for diversity scoring. Used by HSJ; ignored by the non-HSJ SPORES objectives |

A cross-field validator enforces the method/objectives constraint when `enabled = true`:

- `method = "spores"` with empty `objectives` $\to$ `ValueError`
- `method = "mga"` with populated `objectives` $\to$ `ValueError` (probably meant to switch method)
- `enabled = false` $\to$ the validator is bypassed (so YAML drafts can keep both fields populated)

## SporesObjective

String enum of the SPORES objective menu. Each value names an LP objective installed by `apply_spores_objective!` in `mga.jl`.

| Value | Sense | Formulation tag | Description |
|-------|-------|-----------------|-------------|
| `"hsj_diversity"` | $\max$ | [MGA-4](../formulation/capacity-expansion.md#154-diversity-objective) | Classical Hop-Skip-Jump diversity score, retained for use inside a SPORES sweep |
| `"min_total_build"` | $\min$ | [SPORES-1](../formulation/capacity-expansion.md#1510-minimum-total-build-objective) | Smallest near-optimal portfolio |
| `"max_tech_equity"` | $\min$ (min-max) | [SPORES-2](../formulation/capacity-expansion.md#1511-technology-equity-objective) | Min-max over per-technology totals |
| `"max_regional_equity"` | $\min$ (min-max) | [SPORES-3](../formulation/capacity-expansion.md#1512-regional-equity-objective) | Min-max over per-node totals (the spatially-explicit objective) |
| `"evolutionary_dist"` | $\max$ | [SPORES-4](../formulation/capacity-expansion.md#1513-evolutionary-distance-objective) | L1 distance from cost-optimal plan |

---

## MetaNetworkConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `systems` | list[str] | Required | System names |
| `systems_links` | list[SystemLinkConfig] | `[]` | Inter-system connections |
| `dynamic_transfer_pricing` | bool | `true` | Dynamic transfer pricing |
| `inter_system_loss_segments` | int (0-5) | 2 | PWL segments for inter-system transmission losses (0 = linear fallback) |

---

## SystemLinkConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `systems` | list[str] | Required | Connected system pair |
| `connections` | list[list[int]] | Required | Node index pairs |
| `existing_capacity_MW` | list[float] | Required | Existing capacity per connection |
| `max_investment_MW` | list[float] | Required | Max new capacity per connection |
| `investment_cost_per_MW` | list[float] | Required | Investment cost per connection |
| `loss_factor` | list[float] | Required | Loss fraction per connection (used for linear fallback) |
| `reactance_pu` | list[float] | `[0.01]` per link | Series reactance per link (p.u.) for DC-OPF loss model |
| `resistance_pu` | list[float] | `[0.001]` per link | Series resistance per link (p.u.) for DC-OPF loss model |
| `distance_km` | list[float] | Required | Distance per connection |
| `cost_per_mw_km` | list[float] | Required | Distance-dependent cost |

---

## SystemConfig

### General Parameters

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | Required | System identifier |
| `demand_path` | str \| None | None | Path to demand data file |
| `demand_scale` | float (>0) | 1.0 | Demand multiplier |
| `loss_demand_threshold` | float (0-1) | 0.05 | Acceptable unserved energy ratio |
| `discount_rate` | float (0-1) | 0.05 | NPV discount rate |
| `base_lcoe` | float (>=0) | 93.0 | Baseline LCOE ($/MWh) |
| `simulation_mode` | str | Inherited | Override for this system |
| `power_flow_mode` | `"dcopf"` \| `"acopf_soc"` \| `"acopf_qc"` \| `"acopf_polar"` \| `"acopf_rect"` | `"dcopf"` | Power flow formulation |

### Renewable Energy Targets

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `target_re_penetration` | float (0-1) | 1.0 | Final-year RE target |
| `min_annual_increment` | float (>=0) | 0.01 | Min yearly RE growth |
| `max_annual_increment` | float (>=0) | 0.10 | Max yearly RE growth |

### Cost Parameters

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_annual_system_cost` | float | 20e9 | Annual budget cap ($) |
| `max_npv_penalty_per_mw` | float | 1e6 | NPV overrun penalty ($/MW) |
| `max_decommission_cost_per_mw` | float | 5e5 | Decommissioning cap ($/MW) |
| `force_replacement` | float | -5e5 | Replacement incentive ($/MW) |
| `life_extension_cost_factor` | float (>=0) | 0.20 | Life extension cost multiplier |
| `npv_annual_return_rate` | float (0-1) | 0.15 | Annual return rate |

### System Behavior

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `sim_rooftop` | bool | `false` | Enable rooftop solar |
| `inertia_limit_threshold` | float (>=0) | 0.1 | Minimum inertia |
| `soc_end_tolerance` | float (0-0.5) | 0.05 | SOC end-of-horizon tolerance |
| `min_cycling_ratio` | float (0-1) | 0.8 | Minimum battery cycling |
| `min_cycling_period_days` | float (>0) | 7.0 | Cycling evaluation period |
| `reserve_static_default_ratio` | float (0-1) | 0.15 | Default static reserve |
| `reserve_margin` | float (>=1) | 1.15 | Capacity adequacy margin |
| `flexible_demand_benefit_ratio` | float (0-1) | 0.5 | Flexible demand benefit |
| `demand_shift_cost_rate` | float (>=0) | 0.1 | Cost of demand shifting |
| `dynamic_reserve_contribution` | float (0-1) | 0.5 | Battery dynamic reserve |

### Sub-Configurations

| Field | Type | Description |
|-------|------|-------------|
| `nodes` | NodeConfig | Network topology |
| `buses` | list[BusConfig] | Electrical buses |
| `dc_power_flow` | DCPowerFlowConfig | DC power flow settings |
| `ac_power_flow` | ACPowerFlowConfig | AC power flow settings |
| `fuels` | dict[str, FuelConfig] | Fuel definitions |
| `penalties` | PenaltiesConfig | Penalty coefficients |
| `co2_budget` | CO2BudgetConfig | CO2 budget |
| `criticality_penalties` | CriticalityPenalties | Load criticality |
| `generators` | dict[str, GeneratorConfig] | Generators |
| `batteries` | dict[str, BatteryConfig] | Batteries |
| `technologies` | list[TechnologyConfig] | Technology investment candidates |
| `battery_technologies` | list[BatteryTechnologyConfig] | Battery technology investment candidates |
| `electrolyzers` | dict[str, ElectrolyzerConfig] | Electrolyzers |
| `electric_demand` | dict[str, DemandSectorConfig] | Demand sectors |
| `sector_distribution` | dict[int, dict[str, float]] | Per-node sector shares |
| `non_electric_demand` | dict[str, NonElectricDemandConfig] | Non-electric loads |
| `primary_energy_sources` | dict[str, PrimaryEnergySourceConfig] | Fuel sources |
| `ev_categories` | dict[str, EVCategoryConfig] | EV fleet categories |
| `ev_quantity` | dict[str, list[int]] | EV fleet counts |
| `ev_initial_soc` | list[float] | Initial EV SOC per node |
| `base_patterns` | dict[str, list[float]] | 24h charging patterns |
| `rooftop_solar_config` | RooftopSolarConfig \| None | Rooftop PV settings |
| `stochastic_scenarios` | list[StochasticScenarioConfig] | Scenario definitions |
| `transmission_lines_geo` | list[TransmissionLineGeo] | Line geographic data |
| `transformers` | list[TransformerConfig] | Transformer configs |
| `acdc_converters` | list[ACDCConverterConfig] | AC/DC converters |
| `freq_converters` | list[FrequencyConverterConfig] | Frequency converters |
| `development_zones` | list[DevelopmentZoneConfig] | Development zones |
| `fuel_entry_points` | list[FuelEntryPointConfig] | Fuel import points |

---

## NodeConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `num_nodes` | int \| None | Auto-inferred | Number of nodes |
| `nodes_connections` | list[float] | Required | Flattened NxN adjacency matrix (MW) |
| `reserve_static` | list[float] | `[]` | Per-node static reserve (MW) |
| `reserve_dynamic` | list[float] | `[]` | Per-node dynamic reserve (MW) |
| `reserve_duration` | list[int] | `[]` | Per-node reserve duration (hours) |
| `losses` | list[float] | `[]` | Per-node loss fraction |
| `transference_invest_cost` | list[float] | `[]` | Transfer investment cost ($/MW) |
| `transference_invest_max` | list[float] | `[]` | Max transfer investment (MW) |
| `node_coordinates` | list[GeoCoordinate] \| None | None | Node positions |
| `node_names` | list[str] \| None | None | Node labels |

---

## GeneratorConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | Required | Generator name |
| `type` | `"Renewable"` \| `"Non-renewable"` \| `"Storage"` \| `"Electrolyzer"` | Required | Generator type |
| `fuel` | str | Required | Fuel name |
| `technology` | str \| None | None | Technology reference |
| `reservable` | bool | `true` | Can provide reserves |
| `frequency_hz` | float (>0) | 50.0 | System frequency |
| `current_type` | `"AC"` \| `"DC"` \| `"AC_DC"` | `"AC"` | Electrical connection |
| `availability_file` | str \| None | None | Capacity factor file path |

**Per-node arrays** (length = `num_nodes`):

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `rated_power` | list[float] | MW | Nameplate capacity |
| `min_power` | list[float] | MW | Minimum stable output |
| `life_time` | list[int] | years | Operational lifetime |
| `initial_age` | list[int] | years | Current age |
| `degradation_rate` | list[float] | fraction/year | Annual degradation |
| `decommissioning_cost` | list[float] | $ | Decommissioning cost |
| `ramp_up` | list[float] | MW/h | Max ramp-up rate |
| `ramp_down` | list[float] | MW/h | Max ramp-down rate |
| `min_up` | list[int] | hours | Minimum online time |
| `min_down` | list[int] | hours | Minimum offline time |
| `eff_at_rated` | list[float] | fraction | Efficiency at full load |
| `eff_at_min` | list[float] | fraction | Efficiency at min load |
| `inertia` | list[float] | seconds | Inertia constant |
| `start_up_cost` | list[float] | $ | Per-start cost |
| `fuel_cost` | list[float] | $/MWh | Variable fuel cost |
| `fixed_cost` | list[float] | $/MW/yr | Annual fixed O&M |
| `maintenance_cost` | list[float] | $/MWh | Variable O&M |
| `invest_cost` | list[float] | $/MW | Capital cost |
| `invest_max_power` | list[float] | MW | Max new capacity |

**Cost curve (optional)**:

Generators can have piecewise-linear marginal cost curves instead of flat $/MWh fuel costs. When `fuel_cost_curve` is provided, it overrides the flat `fuel_cost` value for that node.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `fuel_cost_curve` | list[CostCurveConfig] \| None | None | Per-node cost curve (length = `num_nodes`). See [CostCurveConfig](#costcurveconfig) below. |

Example:

```yaml
generators:
  - name: Gas_Turbine
    fuel_cost: [167.12]  # Still works (backward compatible)
    fuel_cost_curve:
      - curve_type: stepwise
        blocks:
          - {fraction: 0.4, price: 150.0}
          - {fraction: 0.3, price: 180.0}
          - {fraction: 0.3, price: 220.0}
```

**Reservoir hydroelectric (optional)**:

Empty lists indicate no reservoir. When `reservoir_capacity` has values, the generator gains a water-energy budget: hydro is dispatched against the reservoir's stored energy (not treated as firm MW) with an explicit water balance — inflow, turbining, pumping, spillage and evaporation — enforced in both the operational and capacity-expansion models. On top of the basic budget, four optional behaviours are available:

- **Minimum environmental flow** (`reservoir_min_release`) — a mandatory release floor, met by turbining and/or spilling.
- **Seasonal storage** — when [TSAM inter-period linking](../gui/global-settings.md) is enabled, the reservoir level is chained chronologically across representative periods, so water banked in a wet season is available in a later dry one (instead of being cyclic within each period).
- **Hydraulic cascade** (`cascade_downstream`, `cascade_delay_hours`) — the water a reservoir releases becomes inflow to a downstream reservoir after a travel delay.
- **Head dependence** (`reservoir_head_min_factor`) — a depleted reservoir has lower head and delivers less peak power.

| Field | Type | Default | Unit | Description |
|-------|------|---------|------|-------------|
| `reservoir_capacity` | list[float] | `[]` | MWh | Reservoir capacity per node |
| `reservoir_initial_level` | list[float] | `[]` | fraction | Initial water level (0-1) |
| `reservoir_min_level` | list[float] | `[]` | fraction | Minimum level (0-1) |
| `reservoir_max_level` | list[float] | `[]` | fraction | Maximum level (0-1) |
| `reservoir_inflow_file` | str \| None | `null` | -- | Inflow time series CSV path |
| `reservoir_turbine_efficiency` | list[float] | `[]` | fraction | Turbine efficiency (0-1) |
| `reservoir_evaporation_rate` | list[float] | `[]` | 1/h | Hourly evaporation rate |
| `reservoir_pump_capacity` | list[float] | `[]` | MW | Pump-back capacity per node |
| `reservoir_pump_efficiency` | list[float] | `[]` | fraction | Pump-back efficiency (0-1) |
| `reservoir_spillage_allowed` | bool | `true` | -- | Allow uncontrolled spillage |
| `reservoir_invest_cost` | list[float] | `[]` | $/MWh | Reservoir expansion cost |
| `reservoir_invest_max` | list[float] | `[]` | MWh | Max reservoir expansion |
| `reservoir_min_release` | list[float] | `[]` | MW-eq | Mandatory minimum / ecological release per node (turbined + spilled). `0` = none |
| `cascade_downstream` | str | `""` | -- | Name of the downstream reservoir generator this unit discharges into. Empty = terminal |
| `cascade_delay_hours` | int | `0` | h | Water travel time before the release reaches the downstream reservoir |
| `reservoir_head_min_factor` | list[float] | `[]` | fraction | Power-availability factor at the minimum level (0-1]. `1.0` = no head effect; below 1.0 the available power scales linearly with the fill level |

---

## BatteryConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | Required | Battery name |
| `type` | `"Storage"` | `"Storage"` | Always "Storage" |
| `fuel` | str | `"None"` | Fuel (typically "None") |
| `reservable` | bool | `true` | Can provide reserves |
| `spillage` | bool | `true` | Allow energy spillage |
| `min_duration_hours` | int \| None | None | Min E/P ratio (hours) |
| `max_duration_hours` | int \| None | None | Max E/P ratio (hours) |
| `current_type` | `"AC"` \| `"DC"` | `"DC"` | Electrical connection |
| `availability_file` | str \| None | None | Availability profile |

**Per-node arrays** (all from GeneratorConfig above, plus):

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `capacity` | list[float] | MWh | Energy capacity |
| `MaxChargePower` | list[float] | MW | Max charge rate |
| `MaxDischargePower` | list[float] | MW | Max discharge rate |
| `efficiency_charge` | list[float] | fraction | Charging efficiency |
| `efficiency_discharge` | list[float] | fraction | Discharging efficiency |
| `soc_initial` | list[float] | fraction | Initial SOC (0-1) |
| `max_DoD` | list[float] | fraction | Max depth of discharge |
| `invest_cost_energy` | list[float] | $/MWh | Energy capacity cost |
| `invest_max_capacity` | list[float] | MWh | Max energy investment |
| `throughput_degradation_cost` | list[float] | $/MWh | Degradation cost per MWh discharged (default: `[0.0]`) |

**Discharge cost curve (optional)**:

Batteries can have piecewise-linear marginal cost curves for discharge, replacing the flat maintenance cost. When `discharge_cost_curve` is provided, it adds a PWL cost component to discharge power.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `discharge_cost_curve` | list[CostCurveConfig] \| None | None | Per-node discharge cost curve (length = `num_nodes`). See [CostCurveConfig](#costcurveconfig) below. |

---

## CostCurveConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `curve_type` | `"flat"` \| `"linear"` \| `"stepwise"` \| `"exponential"` | `"flat"` | Cost curve type |
| `flat_price` | float | 0.0 | Marginal cost when `curve_type="flat"` ($/MWh) |
| `blocks` | list[CostBlock] | `[]` | Stepwise blocks when `curve_type="stepwise"` |
| `price_at_zero` | float | 0.0 | Marginal cost at zero output when `curve_type="linear"` ($/MWh) |
| `price_at_max` | float | 0.0 | Marginal cost at full output when `curve_type="linear"` ($/MWh) |
| `base_price` | float | 0.0 | Base marginal cost when `curve_type="exponential"` ($/MWh) |
| `scale_factor` | float | 1.0 | Exponential scale factor when `curve_type="exponential"` |
| `num_segments` | int (2-20) | 5 | Number of PWL segments for linear/exponential approximation |

Each `CostBlock` (used with `curve_type="stepwise"`) has:

| Field | Type | Description |
|-------|------|-------------|
| `fraction` | float | Fraction of rated capacity for this block (all fractions must sum to 1.0) |
| `price` | float | Marginal cost for this block ($/MWh) |

**Curve type summary:**

- **flat**: Single marginal cost across the entire output range. Equivalent to the original `fuel_cost` field. No decomposition into segments.
- **stepwise**: Output is divided into discrete blocks, each with a fixed marginal cost. Blocks are ordered by non-decreasing price. The `fraction` fields define what share of rated capacity each block covers.
- **linear**: Marginal cost increases linearly from `price_at_zero` to `price_at_max`. Approximated as `num_segments` PWL segments.
- **exponential**: Marginal cost follows `base_price * exp(scale_factor * P/P_max)`. Approximated as `num_segments` PWL segments.

**Julia backend behavior:**

The Julia backend creates segment variables (`gseg_{g}_{b}` for generators, `bseg_{bi}_{b}` for batteries). Unit output is decomposed as the sum of segment outputs. Each segment has an upper bound of `fraction * rated_capacity` (adjusted by availability for renewables). The objective function applies per-segment marginal costs instead of a flat fuel cost. For `linear` and `exponential` curve types, the Python converter discretizes the continuous curve into `num_segments` stepwise blocks before passing them to Julia.

**Example --- linear cost curve:**

```yaml
generators:
  - name: CCGT
    fuel_cost_curve:
      - curve_type: linear
        price_at_zero: 120.0
        price_at_max: 200.0
        num_segments: 4
```

Produces 4 segments with marginal costs 130, 150, 170, 190 $/MWh, each covering 25% of rated capacity.

**Example --- exponential cost curve:**

```yaml
generators:
  - name: Peaker
    fuel_cost_curve:
      - curve_type: exponential
        base_price: 100.0
        scale_factor: 1.5
        num_segments: 5
```

Approximates `100 * exp(1.5 * P/P_max)` with 5 PWL segments, producing marginal costs from ~100 $/MWh at low output to ~448 $/MWh at full output.

---

## TechnologyConfig

Candidate technology for per-technology investment in the master problem. Unlike `GeneratorConfig` (existing units), technologies represent new capacity that can be built at any node.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | Required | Technology name |
| `type` | `"Renewable"` \| `"Non-renewable"` | Required | Technology type |
| `fuel` | str | Required | Fuel name |
| `invest_cost` | list[float] | Required | Per-node investment cost ($/MW) |
| `invest_max` | list[float] | Required | Per-node max investment (MW) |
| `fuel_cost` | list[float] | Required | Per-node fuel cost ($/MWh) |
| `fixed_cost` | list[float] | Required | Per-node fixed O&M ($/MW/year) |
| `maintenance_cost` | list[float] | Required | Per-node variable O&M ($/MWh) |
| `life_time` | list[int] | Required | Per-node lifetime (years) |
| `degradation_rate` | list[float] | `[0.0]` | Per-node annual degradation |
| `availability_file` | str \| None | None | Capacity factor file path (renewable only) |
| `fuel_cost_curve` | list[CostCurveConfig] \| None | None | Per-node cost curve. See [CostCurveConfig](#costcurveconfig). |

---

## BatteryTechnologyConfig

Candidate battery/storage technology for per-technology investment in the master problem.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | Required | Technology name |
| `invest_cost_power` | list[float] | Required | Power investment cost per node ($/MW) |
| `invest_cost_energy` | list[float] | Required | Energy investment cost per node ($/MWh) |
| `invest_max_power` | list[float] | Required | Max power investment per node (MW) |
| `invest_max_capacity` | list[float] | Required | Max capacity investment per node (MWh) |
| `min_duration_hours` | float (>=0) | 1.0 | Min energy-to-power ratio (hours) |
| `max_duration_hours` | float (>=0) | 24.0 | Max energy-to-power ratio (hours) |
| `efficiency_charge` | list[float] | Required | Charge efficiency per node (0-1) |
| `efficiency_discharge` | list[float] | Required | Discharge efficiency per node (0-1) |
| `degradation_rate` | list[float] | Required | Degradation rate per node (fraction/year) |
| `lifetime` | int | Required | Economic lifetime (years) |
| `soc_initial` | list[float] | `[0.5]` | Initial SOC fraction per node |
| `max_DoD` | list[float] | `[0.9]` | Max depth of discharge per node |
| `maintenance_cost` | list[float] | `[0.0]` | Maintenance cost per node ($/MWh) |
| `inertia` | list[float] | `[0.0]` | Inertia constant per node (s) |
| `throughput_degradation_cost` | list[float] | `[0.0]` | Cycling wear cost ($/MWh) |
| `spillage` | bool | `true` | Allow energy spillage |
| `current_type` | `"AC"` \| `"DC"` | `"DC"` | Current type |
| `decommissioning_cost` | list[float] | `[0.0]` | Decommissioning cost ($/MW) |

---

## FuelConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | Required | Fuel identifier |
| `unit` | str \| None | None | Physical unit |
| `emission_factor` | float (>=0) | Required | CO2 (tCO2/MWh) |
| `energy_content` | float (>=0) \| None | None | MWh per unit |
| `price_base` | float (>=0) | Required | Base price ($/unit) |
| `price_growth_rate` | float | 0 | Annual price growth |

---

## PenaltiesConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `loss_of_load` | float | 10,000,000 | $/MWh unserved energy |
| `loss_of_reserve_static` | float | 100 | $/MW static reserve deficit |
| `loss_of_reserve_dynamic` | float | 100 | $/MW dynamic reserve deficit |
| `loss_of_inertia` | float | 200 | $/MW*s inertia deficit |
| `transfer_margin` | float | 100 | $/MW transfer overload |
| `curtailment` | float | 100 | $/MWh curtailment (legacy) |
| `max_curtailment_ratio` | float (0-1) | 0.05 | Max curtailment fraction |
| `rooftop_curtailment` | float | 5 | $/MWh rooftop curtailment |
| `co2_cost` | float | 10 | $/tCO2 carbon cost |
| `co2_budget_violation` | float | 500 | $/tCO2 budget overrun |
| `fre_penetration_loss` | float | 100 | $/MWh RE shortfall |
| `ev_loss` | float | 10 | $/MWh EV demand unmet |
| `loss_of_fuel_supply` | float | 100 | $/MW fuel deficit |
| `transport_congestion` | float | 100 | $/MW congestion |
| `storage_violation` | float | 100 | $/MWh storage violation |
| `non_electric_demand_loss` | float | 100 | $/unit demand unmet |
| `soc_violation` | float | 1,000,000 | $/MWh SOC violation |
| `delay_retirement_per_mw` | float | 50,000 | $/MW retirement delay |

---

## CO2BudgetConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enforce CO2 budget |
| `annual_budget` | float (>=0) | 1,000,000 | tonnes CO2/year |

---

## CriticalityPenalties

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `critical` | float | 1000 | Highest priority ($/MWh) |
| `high` | float | 100 | High priority |
| `medium` | float | 10 | Medium priority |
| `low` | float | 1 | Lowest priority |

---

## DCPowerFlowConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `base_impedance` | float | 100.0 | Base impedance (Ohm) |
| `reactance_per_km` | float | 0.4 | Reactance (Ohm/km) |
| `voltage_level_kv` | float | 220.0 | Nominal voltage (kV) |
| `enable_angle_limits` | bool | `true` | Enforce angle limits |
| `max_angle_diff_deg` | float (0-90) | 30.0 | Max angle difference |
| `slack_bus` | int (>=0) | 0 | Reference bus index |
| `loss_model` | `"none"` \| `"linear"` \| `"pwl"` | `"pwl"` | Transmission loss model |
| `pwl_loss_segments` | int (1-10) | 3 | PWL segments for operational dispatch |
| `pwl_loss_segments_master` | int (1-5) | 2 | PWL segments for master problem |

---

## ACPowerFlowConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable AC power flow check |
| `max_iterations` | int (>=1) | 50 | Newton-Raphson iterations |
| `tolerance` | float (>0) | 1e-6 | Convergence tolerance |
| `base_mva` | float (>0) | 100.0 | System base MVA |
| `voltage_min_pu` | float (0-1) | 0.90 | Min voltage (p.u.) |
| `voltage_max_pu` | float (>=1) | 1.10 | Max voltage (p.u.) |
| `check_hours` | `"all"` \| `"peak"` \| `"sample"` | `"peak"` | Hours to check |
| `sample_count` | int (>=1) | 24 | Sample count |
| `default_power_factor` | float (0-1) | 0.9 | Default generator power factor |
| `load_power_factor` | float (0-1) | 0.9 | Load power factor for reactive demand estimation |
| `q_slack_penalty` | float (>=0) | 100.0 | Reactive power slack penalty ($/MVAr) |
| `min_reactance_pu` | float (>0) | 0.01 | Minimum branch reactance clamp (p.u.) |
| `tap_ratio_min` | float (>0) | 0.5 | Transformer tap lower bound (taps below are reset to 1.0) |
| `tap_ratio_max` | float (>0) | 2.0 | Transformer tap upper bound (taps above are reset to 1.0) |
| `q_min_ratio` | float (0-1) | 0.5 | Q_min = -ratio × Q_max when Q limits not specified |

---

## BusConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `bus_id` | str \| None | None | Bus identifier |
| `name` | str | `""` | Bus name |
| `parent_node` | int (>=0) | 0 | Parent node index |
| `voltage_kv` | float | 220.0 | Voltage level (kV) |
| `frequency_hz` | float | 50.0 | Frequency (Hz) |
| `current_type` | `"AC"` \| `"DC"` | `"AC"` | Current type |
| `bus_type` | `"PQ"` \| `"PV"` \| `"slack"` | `"PQ"` | Bus type |
| `demand_fraction` | float | 1.0 | Share of node demand |

---

## TransmissionLineGeo

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `line_id` | str \| None | None | Line identifier |
| `from_node` | int (>=0) | Required | Origin node |
| `to_node` | int (>=0) | Required | Destination node |
| `from_bus` | int \| None | None | Origin bus |
| `to_bus` | int \| None | None | Destination bus |
| `capacity_mw` | float \| None | None | Line capacity (MW) |
| `waypoints` | list[GeoCoordinate] | `[]` | Route coordinates |
| `voltage_kv` | float \| None | None | Line voltage |
| `line_type` | `"overhead"` \| `"underground"` \| `"submarine"` \| None | None | Construction type |
| `length_km` | float \| None | None | Line length |
| `reactance_pu` | float \| None | None | Reactance (p.u.) |
| `resistance_pu` | float \| None | None | Resistance (p.u.) |
| `susceptance_pu` | float \| None | None | Susceptance (p.u.) |
| `num_circuits` | int | 1 | Parallel circuits |
| `frequency_hz` | float (>0) | 50.0 | System frequency |
| `current_type` | `"AC"` \| `"DC"` | `"AC"` | Current type |

---

## TransformerConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | Required | Transformer name |
| `from_node` | int (>=0) | Required | HV-side node index (0-indexed) |
| `to_node` | int (>=0) | Required | LV-side node index (0-indexed) |
| `from_bus` | int \| None | None | HV-side bus index (0-indexed), preferred over `from_node` |
| `to_bus` | int \| None | None | LV-side bus index (0-indexed), preferred over `to_node` |
| `from_voltage_kv` | float (>0) | Required | HV-side voltage (kV) |
| `to_voltage_kv` | float (>0) | Required | LV-side voltage (kV) |
| `rated_power_mva` | float (>0) | Required | Rated apparent power (MVA) |
| `impedance_pu` | float (>0) | 0.1 | Series impedance (p.u.) |
| `resistance_pu` | float \| None | None | Series resistance (p.u.), derived from losses if None |
| `losses_fraction` | float (0-1) | 0.005 | Load losses fraction |

---

## ACDCConverterConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | Required | Converter name |
| `converter_type` | `"VSC"` \| `"LCC"` | `"VSC"` | Converter topology |
| `from_node` | int (>=0) | Required | AC-side node index (0-indexed) |
| `to_node` | int (>=0) | Required | DC-side node index (0-indexed) |
| `from_bus` | int \| None | None | AC-side bus index (0-indexed) |
| `to_bus` | int \| None | None | DC-side bus index (0-indexed) |
| `from_voltage_kv` | float (>0) | 220.0 | AC-side voltage (kV) |
| `dc_voltage_kv` | float (>0) | 320.0 | DC-side voltage (kV) |
| `rated_power_mva` | float (>0) | 100.0 | Rated apparent power (MVA) |
| `min_power_mva` | float (>=0) | 0.0 | Minimum operating power (MVA) |
| `efficiency_rectify` | float (0-1) | 0.98 | AC to DC efficiency |
| `efficiency_invert` | float (0-1) | 0.98 | DC to AC efficiency |
| `standby_losses_mw` | float (>=0) | 0.5 | Standby power loss (MW) |
| `reactive_power_min_mvar` | float | -50.0 | Min reactive power (MVAr) |
| `reactive_power_max_mvar` | float | 50.0 | Max reactive power (MVAr) |
| `power_factor` | float (0-1) | 1.0 | Power factor |
| `impedance_pu` | float (>0) | 0.05 | Series impedance (p.u.) |
| `resistance_pu` | float (>=0) | 0.01 | Series resistance (p.u.) |
| `invest_cost` | float (>=0) | 0.0 | Investment cost ($/MW) |
| `fixed_cost` | float (>=0) | 0.0 | Fixed O&M ($/MW/year) |
| `variable_cost` | float (>=0) | 0.0 | Variable O&M ($/MWh) |
| `invest_max_power` | float (>=0) | 0.0 | Max investment (MW) |
| `life_time` | int (>=1) | 30 | Operational lifetime (years) |
| `initial_age` | int (>=0) | 0 | Current age (years) |
| `degradation_rate` | float (>=0) | 0.005 | Annual degradation |

---

## FrequencyConverterConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | Required | Converter name |
| `from_node` | int (>=0) | Required | Frequency-A node index (0-indexed) |
| `to_node` | int (>=0) | Required | Frequency-B node index (0-indexed) |
| `from_bus` | int \| None | None | Frequency-A bus index (0-indexed) |
| `to_bus` | int \| None | None | Frequency-B bus index (0-indexed) |
| `from_frequency_hz` | float (>0) | 50.0 | Source frequency (Hz) |
| `to_frequency_hz` | float (>0) | 60.0 | Target frequency (Hz) |
| `rated_power_mva` | float (>0) | 100.0 | Rated apparent power (MVA) |
| `min_power_mva` | float (>=0) | 0.0 | Minimum operating power (MVA) |
| `efficiency_a_to_b` | float (0-1) | 0.98 | A-to-B efficiency |
| `efficiency_b_to_a` | float (0-1) | 0.98 | B-to-A efficiency |
| `standby_losses_mw` | float (>=0) | 0.5 | Standby power loss (MW) |
| `reactive_power_min_mvar` | float | -50.0 | Min reactive power (MVAr) |
| `reactive_power_max_mvar` | float | 50.0 | Max reactive power (MVAr) |
| `impedance_pu` | float (>0) | 0.05 | Series impedance (p.u.) |
| `resistance_pu` | float (>=0) | 0.01 | Series resistance (p.u.) |
| `invest_cost` | float (>=0) | 0.0 | Investment cost ($/MW) |
| `fixed_cost` | float (>=0) | 0.0 | Fixed O&M ($/MW/year) |
| `variable_cost` | float (>=0) | 0.0 | Variable O&M ($/MWh) |
| `invest_max_power` | float (>=0) | 0.0 | Max investment (MW) |
| `life_time` | int (>=1) | 30 | Operational lifetime (years) |
| `initial_age` | int (>=0) | 0 | Current age (years) |
| `degradation_rate` | float (>=0) | 0.005 | Annual degradation |

---

## DevelopmentZoneConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | Required | Zone name |
| `technology` | str | Required | Technology type (e.g., Solar, Wind, Battery) |
| `layer` | `"electrical"` \| `"primary_energy"` | `"electrical"` | Network layer |
| `polygon` | list[GeoCoordinate] | Required | Boundary vertices (closed ring) |
| `max_capacity_mw` | float (>=0) \| None | None | Maximum deployable capacity (MW) |
| `notes` | str \| None | None | Free-text notes |
| `line_cost_per_mw_km` | float (>=0) | 1500.0 | Transmission interconnection cost ($/MW/km) |
| `transformer_cost_per_mw` | float (>=0) | 50000.0 | Step-up transformer cost ($/MW) |
| `target_bus` | int \| None | None | Override nearest bus detection (0-indexed bus index) |
| `allowed_generators` | list[str] \| None | None | Generator keys allowed in zone (None = match by technology name) |
| `allowed_technologies` | dict[str, float] \| None | None | Technology name to max capacity mapping |

---

## FuelEntryPointConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | Required | Entry point name |
| `fuel` | str | `""` | Primary fuel (legacy field; use `fuels` for multi-fuel) |
| `fuels` | list[str] | `[]` | List of fuels handled at this point |
| `node` | int (>=0) | Required | Parent node index |
| `coordinate` | GeoCoordinate | Required | Geographic position |
| `max_import_rate` | float (>=0) | 0 | Max import rate (units/hour) |
| `import_cost` | float (>=0) | 0 | Import cost ($/unit) |
| `fuel_params` | dict[str, Any] | `{}` | Additional fuel-specific parameters |

---

## ElectrolyzerConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | Required | Electrolyzer name |
| `type` | `"Electrolyzer"` | `"Electrolyzer"` | Fixed type |
| `fuel` | str | `"Hydrogen"` | Output fuel |
| `technology` | `"PEM"` \| `"Alkaline"` \| `"SOE"` | `"PEM"` | Technology type |
| `energy_per_kg_h2` | float | 50.0 | Specific energy (kWh/kg) |
| `water_cost` | float | 0.001 | Water cost ($/kg H2) |

**Per-node arrays**: `rated_power`, `min_power`, `ramp_up`, `ramp_down`, `eff_at_rated`, `eff_at_min`, `fixed_cost`, `variable_cost`, `invest_cost`, `invest_max_power`, `life_time`, `initial_age`, `degradation_rate`

---

## NonElectricDemandConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `fuel` | str | Required | Fuel type consumed |
| `unit` | str | Required | Physical unit |
| `is_flexible` | bool | `false` | Allows demand shifting |
| `flexibility_ratio` | float (0-1) | 0.0 | Shiftable fraction |
| `criticality` | `"critical"` \| `"high"` \| `"medium"` \| `"low"` | `"medium"` | Shedding priority |
| `delay_tolerance` | int (>=0) | 0 | Max delay (hours) |
| `price_sensitivity` | float (0-1) | 0.0 | Price response |
| `demand` | list[int] | Required | Annual demand per node (units) |

---

## EVCategoryConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `battery_capacity` | float | Required | kWh per vehicle |
| `charging_power` | float | Required | kW per vehicle |
| `v2g_power` | float | Required | V2G power (kW) |
| `v2g_participation` | float (0-1) | Required | V2G participation rate |
| `efficiency_charge` | float (0-1) | Required | Charging efficiency |
| `efficiency_discharge` | float (0-1) | Required | V2G efficiency |
| `min_soc` | float (0-1) | Required | Minimum SOC |
| `max_adoption` | float | 35.0 | Max fleet multiplier |
| `growth_rate` | float | 0.14 | Logistic growth rate |
| `mid_point_fraction` | float (0-1) | 0.5 | S-curve midpoint |

---

## RooftopSolarConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `adoption_scenario` | `"low"` \| `"medium"` \| `"high"` | `"medium"` | Adoption growth |
| `weather_variability` | `"low"` \| `"normal"` \| `"high"` | `"normal"` | Weather variance |
| `simulation_seed` | int | 42 | Random seed |
| `systems_per_node` | list[int] | Required | Rooftop systems per node |
| `avg_system_size` | list[float] | Required | kW per system per node |
| `performance_ratio` | float (0-1) | 0.75 | System PR |
| `degradation_rate` | float (>=0) | 0.005 | Annual degradation |
| `cost_per_kw` | float | 1200 | Installation cost |
| `cost_reduction_rate` | float | 0.08 | Annual cost reduction |
| `o_and_m_cost` | float | 20 | O&M cost ($/kW/yr) |
| `base_year` | int | 2025 | Cost reference year |
| `target_year` | int | 2050 | Target year |
| `initial_adoption` | list[float] | Required | Current adoption per node |
| `max_adoption` | dict[str, float] | Required | Max adoption per scenario |
| `adoption_rates` | dict[str, float] | Required | Growth rates per scenario |

---

## DemandSectorConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `is_flexible` | bool | `false` | Allows demand shifting |
| `flexibility_ratio` | float (0-1) | 0.0 | Shiftable fraction |
| `criticality` | `"critical"` \| `"high"` \| `"medium"` \| `"low"` | `"medium"` | Shedding priority |
| `delay_tolerance` | int (>=0) | 0 | Max delay (hours) |
| `price_sensitivity` | float (0-1) | 0.0 | Price response |

---

## StochasticScenarioConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | Required | Scenario name |
| `probability` | float (0-1) | Required | Scenario weight |
| `description` | str | `""` | Description |
| `multipliers` | ScenarioMultipliers | See below | Cost multipliers |

---

## ScenarioMultipliers

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `invest_cost_renewables` | float | 1.0 | RE investment multiplier |
| `invest_cost_storage` | float | 1.0 | Storage investment multiplier |
| `invest_cost_conventional` | float | 1.0 | Conventional multiplier |
| `invest_cost_transmission` | float | 1.0 | Transmission multiplier |
| `fuel_cost` | float | 1.0 | Fuel cost multiplier |
| `maintenance_cost` | float | 1.0 | Maintenance multiplier |
| `discount_rate` | float | 1.0 | Discount rate multiplier |
| `demand_growth` | float | 1.0 | Demand growth multiplier |
| `fuel_price_growth` | float | 1.0 | Fuel price growth multiplier |
| `carbon_price` | float | 1.0 | Carbon price multiplier |

---

## PrimaryEnergySourceConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | Required | Source name |
| `unit` | str | Required | Physical unit |
| `max_availability` | list[float] | Required | Per-node supply limit (unit/year) |
| `import_cost` | list[float] | Required | Per-node import cost ($/unit) |
| `storage_capacity` | list[float] | Required | Per-node storage (units) |
| `initial_storage_level` | list[float] | Required | Initial level (fraction) |
| `min_storage_level` | float (0-1) | 0.1 | Strategic minimum |
| `storage_investment_cost` | float | Required | $/unit storage |
| `transport_cost` | float | Required | $/unit/km |
| `transport_losses` | float | Required | Loss per 100 km |
| `max_storage_investment_per_node` | float | Required | Max storage investment |
| `max_transport_investment_per_arc` | float | Required | Max transport investment |

---

## GeoCoordinate

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `latitude` | float (-90 to 90) | Required | Latitude (degrees) |
| `longitude` | float (-180 to 180) | Required | Longitude (degrees) |
| `label` | str \| None | None | Optional label |
| `radius_km` | float (0.1-500) | 20.0 | Influence radius |

---

## RiskConfig

Configuration for the risk & resilience module. Nested under the top-level `risk` key in the YAML configuration.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable risk module |
| `risk_measure` | `"expected"` \| `"cvar"` \| `"minimax_regret"` | `"expected"` | Risk measure for optimization |
| `cvar_alpha` | float (0-1) | 0.05 | CVaR confidence level (worst fraction) |
| `cvar_lambda` | float (0-1) | 0.5 | Risk-aversion weight (0 = risk-neutral, 1 = pure CVaR) |
| `risk_criteria` | RiskCriteriaConfig | See below | ALARP classification thresholds |
| `climate_scenarios` | dict[str, ClimateScenarioConfig] | `{}` | SSP climate scenarios |
| `hazard_scenarios` | dict[str, HazardScenarioConfig] | `{}` | Natural hazard scenarios |
| `voll_by_sector` | dict[str, float] | `{}` | Value of Lost Load by sector ($/MWh) |
| `insurance_rate_hazard` | float (>=0) | 0.008 | Annual insurance rate (fraction of replacement cost) |

---

## RiskCriteriaConfig

ALARP (As Low As Reasonably Practicable) risk classification thresholds following ISO 31000:2018 §6.5. Used by `evaluate_risk_criteria()` to classify nodes into risk bands.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `eal_negligible` | float (>=0) | 1,000 | EAL below this is broadly acceptable ($/year) |
| `eal_tolerable` | float (>=0) | 50,000 | EAL above this requires risk reduction ($/year) |
| `eal_intolerable` | float (>=0) | 500,000 | EAL above this requires mandatory action ($/year) |
| `composite_risk_low` | float (0-1) | 0.01 | Composite risk probability threshold for "low" band |
| `composite_risk_medium` | float (0-1) | 0.05 | Composite risk probability threshold for "medium" band |
| `composite_risk_high` | float (0-1) | 0.15 | Composite risk probability threshold for "high" band |

**Classification logic:**

| Classification | Condition | Action Required |
|---------------|-----------|-----------------|
| Negligible | EAL < negligible AND risk < low | No |
| Tolerable low | EAL < tolerable AND risk < medium | No (monitor) |
| Tolerable high | EAL < intolerable AND risk < high | Yes |
| Intolerable | EAL >= intolerable OR risk >= high | Yes (mandatory) |

Example:

```yaml
risk:
  risk_criteria:
    eal_negligible: 1000
    eal_tolerable: 50000
    eal_intolerable: 500000
    composite_risk_low: 0.01
    composite_risk_medium: 0.05
    composite_risk_high: 0.15
```
