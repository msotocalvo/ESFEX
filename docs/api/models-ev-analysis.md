# EV Analysis
Module: `esfex.models.ev_analysis`

Provides the computational engine for Phase B of the EV & V2G Assessment Workflow: charging demand characterization, V2G potential assessment, battery degradation modeling, and grid impact analysis.

---

## Data Structures

### ChargingProfile

24-hour charging demand profile for one vehicle category and scenario.

| Field | Type | Description |
|-------|------|-------------|
| `category` | `str` | Vehicle category (light, medium, heavy, buses) |
| `scenario` | `str` | `"uncontrolled"`, `"tou_shifted"`, or `"optimized"` |
| `hourly_mw` | `list[float]` | 24 values — charging demand per hour (MW) |

### ChargingScenarioResult

Aggregate charging demand for a complete scenario.

| Field | Type | Description |
|-------|------|-------------|
| `scenario` | `str` | Scenario name |
| `profiles_by_category` | `dict[str, ChargingProfile]` | Per-category profiles |
| `aggregate_hourly_mw` | `list[float]` | 24 values — total demand (MW) |
| `peak_demand_mw` | `float` | Maximum hourly demand |
| `daily_energy_mwh` | `float` | Total daily energy consumption |

### V2GPotential

Hourly V2G capacity and energy availability.

| Field | Type | Description |
|-------|------|-------------|
| `hourly_connected_fraction` | `list[float]` | 24 values — fraction of fleet plugged in |
| `max_v2g_power_mw` | `list[float]` | 24 values — max discharge power (MW) |
| `hourly_available_soc_mwh` | `list[float]` | 24 values — available energy in SOC window |
| `daily_v2g_energy_mwh` | `float` | Total daily V2G energy potential |
| `annual_v2g_potential_gwh` | `float` | Annualized V2G potential |

### DegradationResult

Battery degradation analysis output.

| Field | Type | Description |
|-------|------|-------------|
| `chemistry` | `str` | `"NMC"` or `"LFP"` |
| `cycles_per_day` | `float` | Average V2G cycles per day |
| `depth_of_discharge` | `float` | Average DoD for V2G cycling |
| `total_degradation_pct_per_year` | `float` | Total capacity loss (%/year) |
| `degradation_cost_per_kwh` | `float` | Cost per kWh cycled ($/kWh) |
| `breakeven_compensation` | `float` | Break-even V2G rate ($/MWh) |

### GridImpactResult

Grid impact assessment results.

| Field | Type | Description |
|-------|------|-------------|
| `base_demand_24h` | `list[float]` | Base system demand (MW) |
| `ev_charging_24h` | `list[float]` | EV charging demand (MW) |
| `v2g_discharge_24h` | `list[float]` | V2G dispatch (MW) |
| `net_load_24h` | `list[float]` | Net = base + EV - V2G |
| `peak_shaving_mw` | `float` | Peak reduction from V2G |
| `valley_filling_mw` | `float` | Valley filling increase |
| `arbitrage_revenue_annual` | `float` | Annual arbitrage revenue ($) |
| `net_v2g_value` | `float` | Total V2G program value ($) |

---

## Charging Demand Functions

### generate_charging_profiles

```python
def generate_charging_profiles(
    fleet_by_category: dict[str, int],
    ev_categories: dict[str, dict],
    scenario: str = "uncontrolled",
    smart_charging_fraction: float = 0.0,
    base_demand_24h: list[float] | None = None,
) -> ChargingScenarioResult
```

Generate 24-hour charging demand profiles for a given scenario.

**Scenarios:**

| Scenario | Pattern | Description |
|----------|---------|-------------|
| `uncontrolled` | Evening peak | Charge immediately on plug-in. Peak 18:00-22:00 for light vehicles. |
| `tou_shifted` | Night off-peak | Respond to time-of-use tariff signals. Peak 23:00-06:00. |
| `optimized` | Valley filling | Smart charging fills demand valleys to flatten net load. Blends smart and uncontrolled fractions. |

**Charging demand per category:**

```
hourly_mw[h] = pattern[h] * vehicle_count * charging_power_kW / 1000
```

Patterns are empirical 24-hour profiles based on literature for each vehicle category and scenario.

### generate_all_scenarios

```python
def generate_all_scenarios(
    fleet_by_category: dict[str, int],
    ev_categories: dict[str, dict],
    smart_charging_fraction: float = 0.5,
    base_demand_24h: list[float] | None = None,
) -> dict[str, ChargingScenarioResult]
```

Generate all three scenarios at once. Returns a dict keyed by scenario name.

---

## V2G Potential

### compute_v2g_potential

```python
def compute_v2g_potential(
    fleet_by_category: dict[str, int],
    ev_categories: dict[str, dict],
    connected_profile: list[float] | None = None,
    v2g_min_soc: float = 0.30,
    v2g_max_soc: float = 0.90,
) -> V2GPotential
```

Compute hourly V2G discharge capacity and available energy.

**V2G power per hour:**

```
n_v2g = count * connected_fraction[h] * v2g_participation
power_mw = n_v2g * v2g_power_kW * efficiency / 1000
```

**Default connected-time profile**: High at night (0.85-0.90), low during commute (0.25-0.30), medium evening (0.55-0.82).

---

## Battery Degradation

### compute_battery_degradation

```python
def compute_battery_degradation(
    v2g_cycles_per_day: float = 0.5,
    battery_capacity_kwh: float = 50.0,
    depth_of_discharge: float = 0.30,
    chemistry: str = "NMC",
    battery_cost_per_kwh: float | None = None,
) -> DegradationResult
```

Wohler-type battery degradation model:

```
equivalent_cycles = actual_cycles * (DoD / ref_DoD) ^ exponent
cycle_degradation = (annual_eq_cycles / ref_cycles) * 20%
total_degradation = cycle_degradation + calendar_aging
```

**Chemistry parameters:**

| Chemistry | Cycles at 80% DoD | Wohler Exponent | Calendar Aging |
|-----------|-------------------|-----------------|----------------|
| NMC | 2000 | 1.5 | 2.5%/year |
| LFP | 4000 | 1.2 | 1.5%/year |

**Break-even compensation**: The $/MWh rate at which V2G revenue exactly offsets degradation cost.

---

## Grid Impact Assessment

### assess_grid_impact

```python
def assess_grid_impact(
    base_demand_24h: list[float],
    ev_charging_24h: list[float],
    v2g_potential: V2GPotential,
    electricity_prices_24h: list[float] | None = None,
    v2g_compensation_per_mwh: float = 50.0,
    grid_reinforcement_cost_per_mw: float = 500000.0,
) -> GridImpactResult
```

Comprehensive grid impact analysis:

1. **V2G dispatch**: Discharged during the 8 most expensive hours per day.
2. **Peak shaving**: Reduction in system peak from V2G discharge.
3. **Valley filling**: Increase in minimum load from smart EV charging.
4. **Arbitrage revenue**: V2G discharge × electricity price during high-price hours.
5. **Avoided reinforcement**: Peak reduction × grid upgrade cost per MW.

**Synthetic prices** (when not provided): Dual-peak pattern with morning ($90/MWh) and evening ($110/MWh) peaks on a $50/MWh base.

---

## Fleet Evolution Metrics

### compute_fleet_evolution_metrics

```python
def compute_fleet_evolution_metrics(
    years: list[int],
    fleet_ev_by_year: list[int],
    fleet_by_category_by_year: dict[str, list[int]],
    ev_categories: dict[str, dict],
    base_demand_annual_gwh: float = 100.0,
) -> dict
```

Compute yearly metrics for fleet evolution visualization. Returns dict with keys: `years`, `total_ev`, `energy_gwh`, `peak_mw`, `ev_demand_pct`, `v2g_capacity_mw`.
