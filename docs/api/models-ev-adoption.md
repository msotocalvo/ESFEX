# EV Adoption Model
Module: `esfex.models.ev_adoption`

Provides four transport electrification adoption models that project year-by-year EV fleet evolution and energy demand. Each method returns a uniform `EVAdoptionCurve` structure for comparison and downstream integration.

---

## Data Structures

### TransportContext

Baseline vehicle fleet data for the study region.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `country_iso` | `str` | `""` | ISO-3 country code |
| `fleet_by_category` | `dict[str, int]` | `{light: 1000, ...}` | Current vehicle count per category |
| `avg_daily_km` | `dict[str, float]` | `{light: 40, ...}` | Average daily travel distance (km) |
| `energy_consumption` | `dict[str, float]` | `{light: 18, ...}` | Energy consumption (kWh/100km) |
| `charging_stations` | `int` | `0` | Charging stations in study area |
| `road_density_km2` | `float` | `0.0` | Road density (km/km^2) |
| `population` | `int` | `1,000,000` | Study area population |

### EVMacroData

Macroeconomic, cost, and policy inputs.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `gdp_per_capita` | `float` | `5000` | GDP per capita (USD) |
| `urbanization_pct` | `float` | `75` | Urbanization rate (%) |
| `ev_price` | `dict[str, float]` | per category | EV purchase price (USD) |
| `ice_price` | `dict[str, float]` | per category | ICE purchase price (USD) |
| `battery_cost_per_kwh` | `float` | `140` | Battery pack cost ($/kWh) |
| `battery_cost_decline_rate` | `float` | `0.08` | Annual battery cost decline |
| `fuel_price_gasoline` | `float` | `1.20` | Gasoline price ($/L) |
| `fuel_price_diesel` | `float` | `1.10` | Diesel price ($/L) |
| `electricity_tariff` | `float` | `0.15` | Electricity price ($/kWh) |
| `maintenance_diff_annual` | `float` | `500` | Annual maintenance differential (ICE - EV, $) |
| `ice_phaseout_year` | `int` | `0` | ICE ban year (0 = no ban) |
| `ev_subsidy_pct` | `float` | `0.0` | EV purchase subsidy (fraction) |
| `emission_target_pct` | `float` | `0.0` | Emission reduction target (%) |

### EVAdoptionCurve

Uniform output from all adoption methods.

| Field | Type | Description |
|-------|------|-------------|
| `method` | `str` | Method name (`"logistic"`, `"bass"`, `"tco_parity"`, `"policy_driven"`) |
| `years` | `list[int]` | Year labels (inclusive) |
| `penetration` | `list[float]` | EV fleet share [0, 1] per year |
| `fleet_by_category` | `dict[str, list[int]]` | EV count per category per year |
| `total_fleet_ev` | `list[int]` | Total EV count per year |
| `energy_demand_gwh` | `list[float]` | Annual EV energy demand (GWh) |
| `peak_charging_mw` | `list[float]` | Peak simultaneous charging (MW) |
| `parameters` | `dict` | Method-specific parameters used |

---

## Adoption Methods

### 1. Logistic Regression

```python
def run_ev_logistic_adoption(
    macro: EVMacroData,
    transport: TransportContext,
    base_year: int = 2025,
    target_year: int = 2050,
    coefficients: dict | None = None,
) -> EVAdoptionCurve
```

Transport-specific logistic model using macroeconomic and infrastructure drivers:

```
z = beta_0 + beta_fuel * fuel_savings + beta_ev_cost * ev_price_ratio
    + beta_charging * infra_density + beta_gdp * GDP + beta_urban * urban
penetration = 1 / (1 + exp(-z))
```

**Key drivers**: Higher fuel prices, lower EV costs, more charging infrastructure, higher GDP, and urbanization all increase adoption.

### 2. Bass Diffusion

```python
def run_ev_bass_diffusion(
    transport: TransportContext,
    base_year: int = 2025,
    target_year: int = 2050,
    p: float = 0.02,
    q: float = 0.40,
    initial_penetration: float = 0.005,
) -> EVAdoptionCurve
```

Bass innovation/imitation model:

```
F(t) = (1 - exp(-(p+q)*t)) / (1 + (q/p) * exp(-(p+q)*t))
```

- `p` (innovation coefficient): External influence (advertising, policy). Range: 0.01-0.05.
- `q` (imitation coefficient): Word-of-mouth, social influence. Range: 0.30-0.50.

### 3. TCO-Parity

```python
def run_ev_tco_parity(
    macro: EVMacroData,
    transport: TransportContext,
    base_year: int = 2025,
    target_year: int = 2050,
    vehicle_lifetime_years: int = 15,
    price_sensitivity: float = 8.0,
) -> EVAdoptionCurve
```

Compares lifetime Total Cost of Ownership:

```
TCO_EV  = purchase - subsidy + electricity_cost * km/yr + maintenance_ev
TCO_ICE = purchase + fuel_cost * km/yr + maintenance_ice + registration_tax
adoption = sigmoid(sensitivity * (TCO_ICE - TCO_EV) / TCO_ICE)
```

Battery cost decline follows an exponential learning curve, making EVs progressively cheaper over time.

### 4. Policy-Driven

```python
def run_ev_policy_driven(
    macro: EVMacroData,
    transport: TransportContext,
    base_year: int = 2025,
    target_year: int = 2050,
    vehicle_avg_lifetime: int = 15,
) -> EVAdoptionCurve
```

Mandate-based adoption with scrappage model:

- **ICE ban year**: New EV sales share ramps linearly to 100% by ban year.
- **Fleet stock**: Computed from cumulative sales via scrappage model (each cohort survives `vehicle_avg_lifetime` years).
- **No ban**: Uses emission reduction target to derive required EV share trajectory.

---

## Integration Helper

### fit_adoption_to_ev_config

```python
def fit_adoption_to_ev_config(
    curve: EVAdoptionCurve,
    transport: TransportContext,
    num_nodes: int,
    node_demand_fractions: list[float] | None = None,
    charging_profiles: dict[str, list[float]] | None = None,
    v2g_params: dict | None = None,
) -> dict
```

Converts an adoption curve into ESFEX EV configuration parameters:

1. **S-curve fitting**: Uses `scipy.optimize.curve_fit` to fit `max_adoption`, `growth_rate`, `mid_point_fraction` from the penetration trajectory.
2. **Category configuration**: Populates battery capacity, charging power, V2G parameters, and 24-hour base patterns per category.
3. **Node distribution**: Distributes fleet across nodes proportionally to `node_demand_fractions`.
4. **Initial SOC**: Computes per-node initial state of charge in MWh.

**Returns** a dict suitable for populating `GuiEVConfig` with keys: `base_year`, `target_year`, `categories`, `initial_soc`, `fitted_s_curve`, `method`.
