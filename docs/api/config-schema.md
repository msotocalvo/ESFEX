# Config Schema

Module: `esfex.config.schema`

## Model Hierarchy

```
ESFEXConfig
 ├── TemporalConfig
 ├── SolverConfig
 ├── N1SecurityConfig
 ├── MasterProblemConfig
 │    └── MGAConfig
 ├── MetaNetworkConfig
 │    └── SystemLinkConfig
 └── systems: dict[str, SystemConfig]
      ├── NodeConfig
      │    └── GeoCoordinate
      ├── BusConfig
      ├── TransmissionLineGeo
      ├── TransformerConfig
      ├── ACDCConverterConfig
      ├── FrequencyConverterConfig
      ├── DevelopmentZoneConfig
      ├── DCPowerFlowConfig
      ├── ACPowerFlowConfig
      ├── GeneratorConfig
      │    └── CostCurveConfig / CostCurveBlock
      ├── BatteryConfig
      ├── TechnologyConfig
      ├── BatteryTechnologyConfig
      ├── ElectrolyzerConfig
      ├── FuelConfig
      ├── PrimaryEnergySourceConfig
      ├── FuelEntryPointConfig
      ├── FuelInfrastructureConfig
      ├── PenaltiesConfig
      ├── CO2BudgetConfig
      ├── CriticalityPenalties
      ├── DemandSectorConfig
      ├── NonElectricDemandConfig
      ├── EVCategoryConfig
      ├── RooftopSolarConfig
      ├── StochasticScenarioConfig
      │    └── ScenarioMultipliers
      └── ConversionTechnologyConfig
```

---

## ESFEXConfig

Top-level configuration container.

```python
class ESFEXConfig(BaseModel):
    simulation_mode: Literal["development", "unit_commitment"]
    unit_commitment_hours: int = 24
    date_start: str = "01/01/2025 00:00"
    temporal: TemporalConfig
    solver: SolverConfig
    n1_security: N1SecurityConfig
    master_problem: MasterProblemConfig
    enable_primary_energy: bool = True
    meta_network: MetaNetworkConfig
    systems: dict[str, SystemConfig]
    plugins: dict[str, Any] = {}
```

**Key Fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `simulation_mode` | `Literal["development", "unit_commitment"]` | (required) | `"development"` = LP capacity expansion + dispatch; `"unit_commitment"` = MIP dispatch only |
| `date_start` | `str` | `"01/01/2025 00:00"` | Simulation start date (`DD/MM/YYYY HH:MM`) |
| `enable_primary_energy` | `bool` | `True` | Enable fuel supply chain optimization |

**Properties:**

- `primary_system -> SystemConfig`: Returns the first system in the meta-network
- `get_system(name: str) -> SystemConfig`: Retrieve a system by name

---

## SystemConfig

Per-system configuration with all equipment, constraints, and parameters.

```python
class SystemConfig(BaseModel):
    name: str
    demand_path: Optional[str] = None
    demand_scale: float = 1.0
    target_re_penetration: float = 1.0
    discount_rate: float = 0.05
    max_annual_system_cost: float = 20e9
    ...
```

**Key Fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | (required) | System identifier |
| `demand_path` | `Optional[str]` | `None` | Path to demand data file (Excel/CSV) |
| `demand_scale` | `float` | `1.0` | Demand scaling factor |
| `target_re_penetration` | `float` | `1.0` | Target RE share (0-1) |
| `discount_rate` | `float` | `0.05` | Economic discount rate |
| `max_annual_system_cost` | `float` | `20e9` | Annual investment budget cap ($) |
| `loss_demand_threshold` | `float` | `0.05` | Load shedding threshold fraction |
| `sim_rooftop` | `bool` | `False` | Enable rooftop solar simulation |
| `reserve_margin` | `float` | `1.15` | Capacity adequacy margin (1.15 = 15%) |
| `soc_end_tolerance` | `float` | `0.05` | Battery end-of-horizon SOC tolerance |

**Properties:**

- `num_nodes -> int`: Number of nodes (derived from adjacency matrix)
- `num_buses -> int`: Number of buses (0 if none defined)

---

## GeneratorConfig

Generator unit configuration. All per-node fields are lists of length `num_nodes`.

```python
class GeneratorConfig(BaseModel):
    name: str
    type: Literal["Renewable", "Non-renewable", "Storage", "Electrolyzer"]
    fuel: str
    technology: Optional[str] = None
    reservable: bool = True
    rated_power: list[float]           # MW per node
    min_power: list[float]             # Fraction of rated
    fuel_cost: list[float]             # $/MWh
    invest_cost: list[float]           # $/MW (DEPRECATED: use technologies)
    invest_max_power: list[float]      # MW max (DEPRECATED: use technologies)
    life_time: list[int]               # Years
    initial_age: list[int]             # Years
    ...
```

**Per-Node Array Fields:**

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `rated_power` | `list[float]` | MW | Installed capacity |
| `min_power` | `list[float]` | fraction | Minimum output as fraction of rated |
| `fuel_cost` | `list[float]` | $/MWh | Fuel cost |
| `fixed_cost` | `list[float]` | $/MWh | Fixed O&M cost |
| `maintenance_cost` | `list[float]` | $/MWh | Maintenance cost |
| `start_up_cost` | `list[float]` | $ | Startup cost per event |
| `invest_cost` | `list[float]` | $/MW | Investment cost (deprecated) |
| `invest_max_power` | `list[float]` | MW | Max investment capacity (deprecated) |
| `eff_at_rated` | `list[float]` | -- | Efficiency at rated power |
| `eff_at_min` | `list[float]` | -- | Efficiency at minimum power |
| `ramp_up` | `list[float]` | pu/min | Ramp-up rate |
| `ramp_down` | `list[float]` | pu/min | Ramp-down rate |
| `min_up` | `list[int]` | hours | Minimum up time |
| `min_down` | `list[int]` | hours | Minimum down time |
| `life_time` | `list[int]` | years | Economic lifetime |
| `initial_age` | `list[int]` | years | Age at simulation start |
| `degradation_rate` | `list[float]` | %/year | Annual capacity degradation |
| `decommissioning_cost` | `list[float]` | $/MW | Decommissioning cost |
| `inertia` | `list[float]` | s | Inertia constant H |

**Scalar Fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `frequency_hz` | `float` | `50.0` | Operating frequency (Hz) |
| `current_type` | `Literal["AC","DC","AC_DC"]` | `"AC"` | Current type |
| `availability_file` | `Optional[str]` | `None` | Path to availability profile (CSV/Excel) |

**Reservoir Hydroelectric Fields (optional):**

| Field | Type | Description |
|-------|------|-------------|
| `reservoir_capacity` | `list[float]` | Reservoir capacity per node (MWh) |
| `reservoir_initial_level` | `list[float]` | Initial level fraction (0-1) |
| `reservoir_inflow_file` | `Optional[str]` | Inflow time series file |
| `reservoir_pump_capacity` | `list[float]` | Pump capacity per node (MW) |

---

## BatteryConfig

Battery/storage unit configuration.

```python
class BatteryConfig(BaseModel):
    name: str
    type: Literal["Storage"] = "Storage"
    capacity: list[float]              # MWh per node
    MaxChargePower: list[float]        # MW
    MaxDischargePower: list[float]     # MW
    efficiency_charge: list[float]     # [0,1]
    efficiency_discharge: list[float]
    soc_initial: list[float]           # Fraction
    max_DoD: list[float]              # Max depth of discharge
    ...
```

**Storage-Specific Fields:**

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `capacity` | `list[float]` | MWh | Energy capacity per node |
| `MaxChargePower` | `list[float]` | MW | Max charge power per node |
| `MaxDischargePower` | `list[float]` | MW | Max discharge power per node |
| `efficiency_charge` | `list[float]` | -- | Charging efficiency (0-1) |
| `efficiency_discharge` | `list[float]` | -- | Discharging efficiency (0-1) |
| `soc_initial` | `list[float]` | fraction | Initial SOC fraction |
| `max_DoD` | `list[float]` | fraction | Maximum depth of discharge |
| `spillage` | `bool` | -- | Allow battery spillage |
| `min_duration_hours` | `Optional[int]` | hours | Min energy-to-power ratio |
| `max_duration_hours` | `Optional[int]` | hours | Max energy-to-power ratio |
| `throughput_degradation_cost` | `Optional[list[float]]` | $/MWh | Cycling wear cost |

---

## TechnologyConfig

Candidate technology for new generation investment. Unlike `GeneratorConfig` (existing units), technologies define what can be built.

```python
class TechnologyConfig(BaseModel):
    name: str
    type: Literal["Renewable", "Non-renewable"]
    fuel: str
    invest_cost: list[float]           # $/MW per node
    invest_max_power: list[float]      # MW per node
    lifetime: int                      # Years
    eff_at_rated: list[float]
    degradation_rate: list[float]
    availability_file: Optional[str] = None
    ...
```

---

## BatteryTechnologyConfig

Candidate technology for new battery/storage investment.

```python
class BatteryTechnologyConfig(BaseModel):
    name: str
    invest_cost_power: list[float]     # $/MW per node
    invest_cost_energy: list[float]    # $/MWh per node
    invest_max_power: list[float]      # MW per node
    invest_max_capacity: list[float]   # MWh per node
    min_duration_hours: float = 1.0
    max_duration_hours: float = 24.0
    efficiency_charge: list[float]
    efficiency_discharge: list[float]
    lifetime: int
    ...
```

---

## TemporalConfig

```python
class TemporalConfig(BaseModel):
    resolution_hours: int = 1          # 1 = hourly, 3 = 3-hourly, etc.
    rolling_horizon_hours: int = 48    # Window size
    overlap_hours: int = 6             # Overlap between windows
    use_rolling_horizon: bool = True
    investment_resolution: int = 8760  # Hours per investment period
    primary_energy_resolution: int = 24
```

---

## SolverConfig

```python
class SolverConfig(BaseModel):
    name: Literal["highs", "cbc", "glpk", "gurobi", "cplex", "scip", "xpress"] = "highs"
    threads: int = 4
    time_limit: int = 10800            # Seconds (0 = unlimited)
    gap: float = 0.01                  # MIP optimality gap
    verbose: bool = False
    scale_constraints: bool = True
    options: dict[str, Any] = {}       # Solver-specific options
```

---

## PenaltiesConfig

Penalty costs for constraint violations. All values in $/MW or $/MWh.

```python
class PenaltiesConfig(BaseModel):
    loss_of_load: float = 10e6
    loss_of_reserve_static: float = 100
    loss_of_reserve_dynamic: float = 100
    loss_of_inertia: float = 200
    curtailment: float = 100
    max_curtailment_ratio: float = 0.05    # Constraint-based (5% of RE gen max)
    co2_cost: float = 10                   # $/tCO2
    co2_budget_violation: float = 500      # $/tCO2 over budget
    fre_penetration_loss: float = 100      # $/MWh RE shortfall
    ev_loss: float = 10                    # $/MWh EV demand not met
    soc_violation: float = 1e6             # $/MWh SOC limit violation
    delay_retirement_per_mw: float = 50000 # $/MW delay retirement
    ...
```

---

## MasterProblemConfig

```python
class MasterProblemConfig(BaseModel):
    stochastic: bool = False
    representative_days: int = 5
    min_day_separation: int = 5
    use_tsam: bool = False              # Enable TSAM clustering
    tsam_num_periods: int = 10
    tsam_method: Literal["kmedoids", "kmeans"] = "kmedoids"
    tsam_inter_period_linking: bool = True
    use_uc_in_dispatch: bool = False    # UC in operational dispatch
    planning_mode: Literal["perfect_foresight", "myopic"] = "perfect_foresight"
    mga: MGAConfig = MGAConfig()
```

---

## MGAConfig

Configuration for near-optimal alternative generation. Two methods share most fields; the cross-field validator enforces that `objectives` and `method` agree (see [Validation](#mgaconfig-validation)).

```python
class MGAConfig(BaseModel):
    enabled: bool = False
    method: Literal["mga", "spores"] = "mga"
    objectives: list[SporesObjective] = []      # SPORES only
    num_alternatives: int = 10                  # 1-100, MGA only
    slack_fraction: float = 0.05                # 0-0.5
    investment_threshold: float = 0.1
```

### MGAConfig validation

A `@model_validator(mode="after")` activates when `enabled = True`:

| Condition | Outcome |
|-----------|---------|
| `method = "spores"` and `objectives` empty | `ValueError` (choose at least one objective) |
| `method = "mga"` and `objectives` non-empty | `ValueError` (objectives only valid with SPORES) |
| `enabled = False` | validator is bypassed (YAML drafts can keep both populated) |

## SporesObjective

```python
class SporesObjective(str, Enum):
    HSJ_DIVERSITY       = "hsj_diversity"
    MIN_TOTAL_BUILD     = "min_total_build"
    MAX_TECH_EQUITY     = "max_tech_equity"
    MAX_REGIONAL_EQUITY = "max_regional_equity"
    EVOLUTIONARY_DIST   = "evolutionary_dist"
```

The lowercase snake_case values match the Julia `Symbol` keys used by `apply_spores_objective!` in `mga.jl` (e.g. `"min_total_build"` $\leftrightarrow$ `:min_total_build`) so round-tripping through YAML, the Pydantic schema, and the bridge layer is lossless. See [Capacity Expansion §15.10–§15.13](../formulation/capacity-expansion.md#1510-minimum-total-build-objective) for the LP formulation of each objective.

---

## EVCategoryConfig

```python
class EVCategoryConfig(BaseModel):
    battery_capacity: float             # kWh per vehicle
    charging_power: float               # kW per vehicle
    v2g_power: float                    # kW per vehicle for V2G
    v2g_participation: float            # Fraction (0-1)
    efficiency_charge: float            # 0-1
    efficiency_discharge: float         # 0-1
    min_soc: float                      # 0-1
    max_adoption: float = 35.0          # Growth multiplier
    growth_rate: float = 0.14           # Logistic growth rate
    mid_point_fraction: float = 0.5     # S-curve midpoint position
```

---

## NodeConfig

```python
class NodeConfig(BaseModel):
    num_nodes: Optional[int] = None     # Auto-inferred from adjacency matrix
    nodes_connections: list[float]      # Flattened NxN adjacency matrix (MW)
    reserve_static: list[float] = []    # Static reserve per node (MW)
    reserve_dynamic: list[float] = []   # Dynamic reserve per node (MW)
    losses: list[float] = []            # Transmission losses per node
    node_coordinates: Optional[list[GeoCoordinate]] = None
    node_names: Optional[list[str]] = None
```

---

## DCPowerFlowConfig

```python
class DCPowerFlowConfig(BaseModel):
    base_impedance: float = 100.0       # Ohm
    reactance_per_km: float = 0.4       # Ohm/km
    voltage_level_kv: float = 220.0     # kV
    enable_angle_limits: bool = True
    max_angle_diff_deg: float = 30.0    # Degrees (0-90)
    slack_bus: int = 0                  # 0-indexed
    loss_model: Literal["none", "linear", "pwl"] = "pwl"
    pwl_loss_segments: int = 3          # Operational dispatch (1-10)
    pwl_loss_segments_master: int = 2   # Master problem (1-5)
```

---

## Other Configuration Classes

### N1SecurityConfig

```python
class N1SecurityConfig(BaseModel):
    enabled: bool = False
    apply_to_modes: list[str] = ["unit_commitment"]
    transmission_enabled: bool = True
    transmission_reserve_factor: float = 0.70
    generation_enabled: bool = True
    generation_reserve_type: Literal["largest_unit", "percentage"] = "largest_unit"
    generation_reserve_percentage: float = 0.15
```

### MetaNetworkConfig

```python
class MetaNetworkConfig(BaseModel):
    systems: list[str]                  # System names
    systems_links: dict[str, SystemLinkConfig] = {}
    dynamic_transfer_pricing: bool = False
```

### StochasticScenarioConfig

```python
class StochasticScenarioConfig(BaseModel):
    name: str
    probability: float                  # 0-1
    description: str = ""
    multipliers: ScenarioMultipliers = ScenarioMultipliers()
```

### RooftopSolarConfig

```python
class RooftopSolarConfig(BaseModel):
    adoption_scenario: Literal["low", "medium", "high"] = "medium"
    weather_variability: Literal["low", "normal", "high"] = "normal"
    simulation_seed: int = 42
    systems_per_node: list[int]
    avg_system_size: list[float]        # kW per system
    performance_ratio: float = 0.75
    cost_per_kw: float = 1200
    cost_reduction_rate: float = 0.08
    initial_adoption: list[float]
    max_adoption: dict[str, float]
```

### CostCurveConfig

Bidding/offer curve configuration for generators and batteries.

```python
class CostCurveConfig(BaseModel):
    curve_type: Literal["flat", "linear", "stepwise", "exponential"] = "flat"
    flat_price: Optional[float] = None
    blocks: Optional[list[CostCurveBlock]] = None
    price_at_zero: Optional[float] = None
    price_at_max: Optional[float] = None
    base_price: Optional[float] = None
    scale_factor: Optional[float] = None
    num_segments: int = 5
```

### ElectrolyzerConfig

```python
class ElectrolyzerConfig(BaseModel):
    name: str
    type: Literal["Electrolyzer"] = "Electrolyzer"
    technology: Literal["PEM", "Alkaline", "SOE"] = "PEM"
    energy_per_kg_h2: float = 50.0      # kWh/kg H2
    water_cost: float = 0.001           # $/kg H2
    ...
```

---

## Validation Example

```python
from esfex.config.schema import ESFEXConfig, SystemConfig, GeneratorConfig

# Pydantic validates all fields at construction
try:
    gen = GeneratorConfig(
        name="Solar PV",
        type="Renewable",
        fuel="Sun",
        rated_power=[50.0, 30.0],       # 2 nodes
        min_power=[0.0, 0.0],
        life_time=[25, 25],
        initial_age=[0, 0],
        # ... other required fields
    )
except ValidationError as e:
    print(e)  # Missing required fields are reported
```

See the [Config Reference](../reference/config-reference.md) for a complete listing of all fields with YAML examples.
