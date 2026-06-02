# EV & V2G Assessment

Electric vehicle fleet adoption modeling and vehicle-to-grid integration analysis. Access via **Workflows > EV & V2G Assessment**.

The wizard evaluates the electrification trajectory of a vehicle fleet and its consequences for the power system. Starting from a geographic domain and baseline fleet composition, it projects EV adoption under multiple scenarios, generates hourly charging demand profiles, quantifies V2G flexibility potential, and produces calibrated EV parameters for the ESFEX optimizer. The analysis builds on established frameworks for V2G economics [**[51]**](../reference/bibliography.md#ref51), power system integration of electric vehicles [**[52]**](../reference/bibliography.md#ref52), and technology diffusion modeling [**[53]**](../reference/bibliography.md#ref53).

The wizard is organized in two phases:

- **Phase A (Steps 1-5)**: Fleet Assessment. Define the transport context, configure macroeconomic and policy inputs, run four adoption models, review fleet evolution, and select a baseline scenario.
- **Phase B (Steps 6-9)**: Grid Integration. Generate charging demand profiles under three management strategies, assess V2G technical potential and battery degradation, evaluate grid impact, and export the results to the ESFEX model.

All computations use the `evrex` library, which can also be used independently for scripting and batch analysis — see [Scripting](#scripting).


---


## Phase A — Fleet Assessment


### Step 1: Transport Context

Define the geographic study area and enter the baseline vehicle fleet composition.

**Domain definition:**

Draw a rectangle on the interactive map or enter bounding box coordinates manually (south latitude, north latitude, west longitude, east longitude). The domain defines the geographic scope for infrastructure auto-detection and country identification.

- **Draw on Map**: Click the button, then draw a rectangle on the map. The wizard minimizes during drawing and restores when the rectangle is complete.
- **Manual coordinates**: Enter the four bounding-box values directly and click **Apply Coordinates**.

**Auto-detect infrastructure:**

Once a domain is defined, click **Fetch OSM Data** to query the OpenStreetMap Overpass API for two indicators:

| Indicator | Source | Description |
|-----------|--------|-------------|
| Charging stations | `amenity=charging_station` | Number of public EV charging points within the domain |
| Road density | Highway network length | Total road length (km) divided by domain area (km^2) |

Both queries run in parallel. A progress bar tracks completion, and results are displayed below the button (e.g., "42 charging stations, road density: 3.2 km/km^2"). These indicators provide context for the adoption models — higher charging infrastructure density generally correlates with faster EV uptake.

**Fleet entry table:**

Enter the current vehicle fleet for four categories. Each row represents a vehicle class with three parameters:

| Category | Fleet Count | Avg Daily km | Energy Consumption (kWh/100km) |
|----------|------------|--------------|-------------------------------|
| **Light vehicles** | 1,000 | 40 | 18 |
| **Medium vehicles** | 200 | 80 | 25 |
| **Heavy vehicles** | 50 | 150 | 55 |
| **Buses** | 30 | 200 | 80 |

Default values are shown above. Adjust them to reflect the actual fleet composition in the study area. The fleet count is the total number of vehicles (not just EVs) — the adoption models will project the EV fraction over time.

**Tips:**

- Start with approximate fleet data. The adoption models are most sensitive to relative proportions between categories rather than exact counts.
- The OSM fetch may time out for very large domains. If this happens, reduce the bounding box to the urban core where most charging infrastructure is concentrated.
- Fleet data for many countries is available from national transport statistics or OICA (International Organization of Motor Vehicle Manufacturers).


### Step 2: Macro & Policy Data

Configure macroeconomic indicators, EV-specific cost parameters, and policy instruments that drive the adoption models.

**Macroeconomic indicators:**

| Parameter | Default | Units | Description |
|-----------|---------|-------|-------------|
| GDP per capita | 5,000 | $/year | Gross domestic product per person. Higher GDP correlates with faster EV adoption due to greater purchasing power. |
| Urbanization | 75 | % | Share of population in urban areas. Urban populations have better access to charging infrastructure. |
| Population | 1,000,000 | count | Total population within the study domain. |
| Inflation rate | 3 | %/year | Annual consumer price inflation. Affects real fuel price projections. |
| GDP growth rate | 3 | %/year | Annual real GDP growth. Drives future purchasing power evolution. |

**Auto-fetch**: Click **Fetch Macro Data** to retrieve GDP per capita, urbanization, and population from the World Bank API, and GDP growth and inflation from the IMF DataMapper API. The country is auto-detected from the domain bounding box defined in Step 1 (reverse geocoding). If country detection fails, enter data manually.

**EV economics table:**

Vehicle purchase prices for all four categories, with one column for EV price and one for ICE (internal combustion engine) price:

| Category | EV Price ($) | ICE Price ($) |
|----------|-------------|--------------|
| Light | 35,000 | 25,000 |
| Medium | 55,000 | 40,000 |
| Heavy | 120,000 | 90,000 |
| Buses | 300,000 | 250,000 |

Additional cost parameters:

| Parameter | Default | Units | Description |
|-----------|---------|-------|-------------|
| Battery cost | 140 | $/kWh | Current lithium-ion pack cost. Dominant factor in EV purchase price premium. |
| Battery cost decline | 8 | %/year | Annual reduction in pack cost, following the learning-curve trajectory [**[53]**](../reference/bibliography.md#ref53). |
| Gasoline price | 1.20 | $/liter | Pump price for gasoline (petrol). |
| Diesel price | 1.10 | $/liter | Pump price for diesel. |
| Electricity tariff | 0.150 | $/kWh | Retail electricity price for EV charging. |
| Maintenance difference | 500 | $/year | Annual maintenance cost saving of EV over ICE (fewer moving parts, no oil changes). |

**Fetch Battery Costs**: Retrieves bundled BNEF (BloombergNEF) historical battery pack costs (2013-2024) and projects future costs using the configured annual decline rate.

**Policy instruments:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| ICE ban year | None | Year when new ICE vehicle sales are banned (0 = no ban). Accelerates adoption in the policy-driven model. |
| EV subsidy | 0 % | Government purchase subsidy as a percentage of EV price. Reduces effective purchase cost. |
| Registration tax difference | $0 | Annual registration tax advantage for EVs over ICE vehicles. |
| Emission target | 0 % | Fleet-wide CO2 reduction target (%). Used by the policy-driven model to set adoption pace. |

**Tips:**

- Battery cost and its decline rate are among the most influential parameters for TCO parity timing. The default 8%/year decline is consistent with the historical learning rate of approximately 18% per doubling of cumulative production.
- When the ICE ban year is set (e.g., 2035), the policy-driven model enforces near-100% EV sales by that date, producing a steeper adoption curve than the other methods.
- For small island developing states (SIDS), fuel prices are often 30-50% higher than global averages due to transport costs, which accelerates the TCO crossover.


### Step 3: Adoption Models

Run up to four EV adoption projection methods simultaneously and validate against historical data.

**Method selection:**

Four checkboxes (all enabled by default) control which models are executed:

| Method | Approach | Key Equation |
|--------|----------|-------------|
| **Bass Diffusion** | Innovation/imitation dynamics [**[53]**](../reference/bibliography.md#ref53). Early adopters (innovators) drive initial uptake; social influence (imitators) accelerates growth. | See Eq. [(EV-1)](#bass-diffusion-model) |
| **Logistic** | S-curve regression on macro drivers (fuel savings, GDP, urbanization, charging infrastructure). | See Eq. [(EV-2)](#logistic-adoption-model) |
| **TCO Parity** | Adoption triggered by total-cost-of-ownership crossover between EV and ICE, incorporating battery learning curve. | See Eqs. [(EV-3)](#total-cost-of-ownership), [(EV-4)](#battery-learning-curve) |
| **Policy-Driven** | Base logistic curve modified by ICE ban year, subsidies, and emission targets. Forces near-complete transition by the ban date. | Modified (EV-2) |

**Presets:**

A dropdown offers three parameter packages that simultaneously configure all per-method parameters:

| Preset | Bass p/q | Logistic beta_0 | TCO Sensitivity | Character |
|--------|---------|-----------------|----------------|-----------|
| **Conservative** | 0.01 / 0.30 | -4.0 | 5.0 | Slow start, modest final penetration |
| **Moderate** | 0.02 / 0.40 | -3.5 | 8.0 | Balanced trajectory |
| **Aggressive** | 0.04 / 0.50 | -2.5 | 12.0 | Rapid adoption, high final share |

**Time horizon:**

- **Base year**: Starting year for the projection (default: 2025, range: 2020-2040).
- **Target year**: End year for the projection (default: 2050, range: 2030-2070).

**Validation data:**

Two sources of historical EV stock data for model calibration:

- **Fetch IEA Data**: Loads bundled data from the IEA Global EV Data Explorer (2010-2024 EV stock by country). Available when country detection succeeded in Step 2.
- **Import CSV**: Load a user-provided CSV file with columns `year` and `ev_stock` (or `stock`).

When validation data is available, the fleet results in Step 4 overlay the historical trajectory on the projection curves, and the MAPE (Mean Absolute Percentage Error) metric is computed for each model.

**Run Models**: Executes all selected methods in a background thread. A progress bar and log panel show real-time status. Each method reports its final penetration percentage upon completion.

**Tips:**

- Run all four methods initially to compare their range. The spread between the most conservative and most aggressive projection gives an indication of the structural uncertainty in the forecast.
- The Bass model is particularly suitable when historical adoption data is available for calibration. Without data, it relies on assumed innovation (p) and imitation (q) coefficients.
- For countries with announced ICE bans (e.g., Norway 2025, UK 2035, EU 2035), the policy-driven model produces the most realistic near-term trajectory.


### Step 4: Fleet Results

Visualization of the fleet evolution projections from all models computed in Step 3.

**Charts (side-by-side):**

Two matplotlib panels are displayed:

- **Left panel — Fleet Electrification**: EV fleet share (%) over time for each method, plotted as colored lines (Logistic: orange, Bass: blue, TCO Parity: green, Policy-Driven: red). If validation data was loaded, historical EV stock is overlaid.
- **Right panel — EV Energy Demand**: Total annual EV energy demand (GWh) per method over the projection horizon. This represents the aggregate electricity consumption from EV charging.

**Summary table:**

A tabular view showing penetration percentages at every 5th year (2025, 2030, 2035, ..., 2050) for each method. The final year is always included regardless of the 5-year interval. This provides quick numerical reference points for the curves shown in the chart.

**Export options:**

| Format | Content |
|--------|---------|
| **PNG** | High-resolution (150 DPI) image of both charts. |
| **CSV** | Full time series with columns: year, method, penetration, total_ev, energy_gwh, peak_mw. |


### Step 5: Scenario Selection

Compare all adoption models and select one as the baseline for Phase B grid integration analysis.

**Selection interface:**

Radio buttons are generated for each computed model, labeled with the method name and final penetration:

> *"Bass Diffusion -- 72% by 2050"*
> *"Logistic -- 65% by 2050"*
> *"Tco Parity -- 58% by 2050"*
> *"Policy Driven -- 95% by 2050"*

**Comparison table:**

| Column | Description |
|--------|-------------|
| Method | Adoption model name |
| Final % | EV fleet share at the target year |
| Total EVs | Absolute number of electric vehicles at target year |
| Energy (GWh) | Annual EV energy demand at target year |
| Peak (MW) | Peak charging demand at target year |

Select the scenario that best represents the expected adoption trajectory for the study area. This curve becomes the basis for all Phase B computations — charging demand profiles, V2G potential, and grid impact are all derived from the fleet numbers in the selected scenario.

**Tips:**

- If IEA validation data was loaded, prefer the model with the lowest MAPE for the historical period.
- For policy analysis, selecting the policy-driven model allows exploring the grid impact of committed government targets.
- For conservative grid planning, the model with the highest peak demand provides a stress-test scenario.


---


## Phase B — Grid Integration


### Step 6: Charging Demand

Generate and compare 24-hour charging demand profiles under three charging management strategies.

**Smart charging fraction:**

A horizontal slider (0-100%, default 50%) controls what fraction of the EV fleet participates in smart charging programs. The remaining fraction charges in an uncontrolled manner. This parameter affects the Time-of-Use Shifted and Optimized scenarios but not the Uncontrolled scenario.

**Base demand (optional):**

Upload a CSV file containing a 24-hour base electricity demand profile (MW) for the study area. The file should contain at least 24 numeric values representing hourly demand from hour 0 to hour 23. If no file is loaded, a synthetic Gaussian-mixture profile is used (twin peaks at 09:00 and 20:00, baseline around 200 MW). The base demand is used as an overlay reference in the chart and as input for the optimized scenario, which shapes EV charging to fill demand valleys.

**Analysis year:**

A year selector (default: midpoint of the projection horizon) determines which year's fleet size is used for profile generation. The fleet count per category at the selected year is extracted from the adoption curve chosen in Step 5.

**Charging parameters by category:**

The following technical parameters are used internally for each vehicle category:

| Category | Charging Power (kW) | Battery Capacity (kWh) |
|----------|---------------------|----------------------|
| Light | 7 | 50 |
| Medium | 11 | 75 |
| Heavy | 22 | 150 |
| Buses | 50 | 300 |

**Three scenarios:**

| Scenario | Charging Pattern | Peak Behavior |
|----------|-----------------|---------------|
| **Uncontrolled** | Vehicles charge immediately upon plug-in, following natural arrival patterns. | Evening peak coincides with residential demand peak (18:00-22:00). |
| **Time-of-Use Shifted** | Charging shifted to off-peak hours using time-of-use tariff signals. | Night valley filling (23:00-06:00). |
| **Optimized** | Charging dispatch optimized to minimize net load variance (valley filling). | Flattened net load curve using the base demand profile as reference. |

**Generate Profiles**: Computes all three scenarios using the `evrex.generate_all_scenarios()` function. Results appear as:

- **24-hour line chart**: Three colored lines (Uncontrolled: red, ToU Shifted: amber, Optimized: green) showing aggregate charging demand (MW) by hour. If base demand was loaded, it appears as a dashed blue overlay.
- **Summary table**: One row per scenario with columns for scenario name, peak demand (MW), and daily energy (MWh).

**Tips:**

- The difference between Uncontrolled and Optimized peak demand quantifies the value of smart charging infrastructure. Typical reductions range from 30-60% of peak.
- Load a real system demand profile for the most accurate optimized scenario. Without it, the synthetic profile may not reflect local load patterns.
- The daily energy (MWh) is identical across all three scenarios — only the temporal distribution changes.


### Step 7: V2G Potential

Assess vehicle-to-grid discharge capacity and quantify battery degradation costs.

**Connected-time profile:**

An editable 4x6 table representing the 24-hour fleet connection profile. Rows correspond to 6-hour blocks (00-05, 06-11, 12-17, 18-23) and columns to individual hours within each block (h+0 through h+5). Each cell contains a fraction (0.00 to 1.00) representing the share of the fleet that is plugged in and available for V2G at that hour.

Default profile characteristics:
- **Night (00-05)**: High connection (~0.85-0.90). Most vehicles parked at home or depot.
- **Morning (06-11)**: Declining connection as vehicles depart for commuting.
- **Midday (12-17)**: Lowest connection (~0.30-0.40). Vehicles in transit or parked without charging access.
- **Evening (18-23)**: Rising connection as vehicles return and plug in.

**V2G parameters:**

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| Min SOC | 0.30 | 0.10-0.60 | Minimum state of charge reserved for driving needs. V2G discharge stops at this level. |
| Max SOC | 0.90 | 0.60-1.00 | Maximum SOC from which V2G discharge begins. |
| V2G cycles/day | 0.5 | 0.1-3.0 | Average number of V2G discharge cycles per day per vehicle. Higher values increase energy provision but accelerate degradation. |
| Battery chemistry | NMC | NMC, LFP | Cell chemistry affects degradation rates. LFP (lithium iron phosphate) has better cycle life than NMC (nickel manganese cobalt). |
| Avg battery capacity | 50 kWh | 20-500 kWh | Fleet-average battery capacity. Used for degradation cost normalization. |

**V2G technical parameters per category** (used internally):

| Category | V2G Power (kW) | V2G Participation | Discharge Efficiency |
|----------|---------------|-------------------|---------------------|
| Light | 5 | 30% | 90% |
| Medium | 8 | 40% | 90% |
| Heavy | 15 | 50% | 90% |
| Buses | 40 | 70% | 90% |

**Run V2G Analysis**: Computes V2G potential using `evrex.compute_v2g_potential()` and battery degradation using `evrex.compute_battery_degradation()`.

**Charts (side-by-side):**

- **Left panel — V2G Discharge Capacity**: Filled area chart showing maximum V2G power (MW) available at each hour of the day. The shape mirrors the connected-time profile, scaled by fleet size, V2G power per vehicle, and participation rate. See Eq. [(EV-6)](#v2g-available-power).
- **Right panel — Fleet Connected Profile**: Bar chart showing the fraction of the fleet connected at each hour. Direct visualization of the editable connection table.

**Degradation summary:**

A text panel below the charts reports:

- **Chemistry**: NMC or LFP.
- **Total degradation**: Percentage capacity loss per year, split into cycle degradation and calendar aging components. See Eqs. [(EV-7)](#battery-degradation-cycle-component), [(EV-8)](#capacity-retention).
- **Degradation cost**: Dollar cost per kWh of V2G energy discharged, accounting for the battery replacement value consumed.
- **Break-even compensation**: Minimum V2G payment ($/MWh) required to cover degradation costs. Below this price, V2G is uneconomic for the vehicle owner.
- **Daily V2G energy**: Total energy available from V2G (MWh/day) across the fleet.
- **Annual V2G potential**: Annualized V2G energy (GWh/year).

**Tips:**

- LFP chemistry typically shows 2-3x better cycle life than NMC, resulting in significantly lower degradation costs and break-even compensation.
- A V2G cycles/day value of 0.5 means one full discharge cycle every two days, which is a moderate participation level. Values above 1.0 represent aggressive V2G programs.
- The connected-time profile has the largest influence on V2G power availability. Workplace charging infrastructure (increasing midday connection) can substantially increase daytime V2G capacity for solar peak shaving.


### Step 8: Grid Impact

Comprehensive assessment of how EV charging and V2G affect the power system.

**Configuration:**

| Setting | Options | Description |
|---------|---------|-------------|
| Charging scenario | Uncontrolled, Time-of-Use Shifted, Optimized | Which charging profile to use for the impact assessment. Default: Optimized. |
| V2G compensation | $0-500/MWh (default: $50) | Payment rate for V2G services. Used to compute economic metrics. |

**Run Grid Analysis**: Executes `evrex.assess_grid_impact()` using the selected charging scenario, V2G potential from Step 7, and the base demand profile from Step 6 (or a synthetic profile if none was loaded).

**Charts (side-by-side):**

- **Left panel — Net Load Profile**: Stacked area chart showing the 24-hour composition of the net load:
  - Blue fill: Base demand (existing grid load).
  - Red fill: EV charging demand added on top.
  - Green fill: V2G discharge subtracted from the total.
  - Black line: Net load (base + EV charging - V2G).

  This visualization reveals when EV charging adds stress to the grid (typically evening hours in the uncontrolled scenario) and when V2G provides relief.

- **Right panel — Load Duration Curve**: Three sorted curves comparing:
  - Base demand alone (blue).
  - Base + EV charging (red).
  - Net load with V2G (green).

  The vertical gap between the red and green curves at the left (peak) end quantifies the peak shaving achieved by V2G. A flatter green curve indicates better load leveling.

**Flexibility quantification table:**

| Metric | Description |
|--------|-------------|
| Peak shaving | Reduction in peak demand (MW) due to V2G: \(\Delta P_{\text{peak}} = P_{\text{peak, no V2G}} - P_{\text{peak, with V2G}}\). See Eq. [(EV-9)](#peak-shaving). |
| Valley filling | Increase in minimum demand (MW) due to smart charging. Measures how much the demand valley is raised. |
| Peak-to-valley ratio (before) | Ratio of maximum to minimum demand before EV integration. Higher values indicate more variable load. |
| Peak-to-valley ratio (after) | Same ratio after EV charging and V2G. A reduction indicates improved load leveling. |
| RE curtailment reduction | Estimated reduction in renewable energy curtailment (%) enabled by flexible EV charging absorbing excess generation. |
| Frequency regulation | V2G capacity (MW) available for fast frequency response services. |

**Economic analysis:**

Three key financial metrics displayed below the flexibility table:

| Metric | Description |
|--------|-------------|
| **Arbitrage revenue (annual)** | Revenue from buying electricity at low prices (charging) and selling at high prices (V2G discharge), computed from hourly price differentials. |
| **Avoided grid reinforcement** | Estimated savings from reduced peak demand, avoiding or deferring transmission and distribution upgrades. |
| **Net V2G program value** | Total program value: arbitrage revenue + avoided reinforcement - V2G compensation payments to vehicle owners. |

**Export**: Save the complete hourly grid impact data (base demand, EV charging, V2G discharge, net load) as a CSV file.

**Tips:**

- Compare the Uncontrolled and Optimized scenarios to quantify the full value of smart charging. The difference in peak shaving represents the infrastructure investment that could be avoided.
- A peak-to-valley ratio improvement from (e.g.) 1.8 to 1.3 indicates significant load leveling, which reduces the need for peaking generation and improves baseload plant utilization.
- The economic metrics are indicative estimates. For detailed financial analysis, export the results and use the [Financial Analysis Wizard](financial-analysis.md).


### Step 9: Integration

Compile all wizard results and apply them to the ESFEX optimization model.

**S-curve fitting:**

The selected adoption curve is fitted to a logistic function using least-squares optimization to extract three parameters compatible with the ESFEX EV module:

| Parameter | Description |
|-----------|-------------|
| `max_adoption` | Maximum fleet capacity (MW equivalent) at saturation |
| `growth_rate` | Logistic growth rate parameter |
| `mid_point_fraction` | Fraction of the planning horizon at which adoption reaches 50% |

These parameters, together with per-category technical specifications, form the complete EV configuration for ESFEX.

**Configuration preview:**

A read-only text panel displays the generated configuration in YAML format (or JSON if PyYAML is not installed). The preview includes:

- A comment header with the method name, projection period, degradation cost, and break-even V2G compensation.
- Per-category parameters: battery capacity, charging power, V2G power, V2G participation, charge/discharge efficiency, min SOC, adoption curve parameters, fleet quantity per year, and normalized charging base pattern (from the optimized scenario).
- Global parameters: initial SOC, base year, target year.

**Actions:**

| Button | Description |
|--------|-------------|
| **Apply to Model** | Populates the `ev_config` section of the currently loaded GUI model with `GuiEVConfig` and `GuiEVCategory` instances. Disabled when no model is open. |
| **Export YAML Snippet** | Save the EV configuration as a standalone YAML file for manual integration into a ESFEX configuration. |
| **Export All** | Save all wizard outputs to a selected directory. Creates the following files: |

**Export All contents:**

| File | Format | Content |
|------|--------|---------|
| `ev_adoption_curve.csv` | CSV | Year-by-year adoption curve: penetration, total EVs, energy demand, peak charging |
| `charging_uncontrolled.csv` | CSV | 24-hour uncontrolled charging profile per category |
| `charging_tou_shifted.csv` | CSV | 24-hour time-of-use shifted profile per category |
| `charging_optimized.csv` | CSV | 24-hour optimized charging profile per category |
| `v2g_analysis.json` | JSON | V2G power availability, daily energy, annual potential, hourly connection fractions |
| `grid_impact.json` | JSON | 24-hour base demand, EV charging, V2G discharge, net load, peak shaving, valley filling, economic metrics |
| `ev_config.yaml` | YAML | Complete EV configuration for ESFEX |
| `degradation_summary.json` | JSON | Chemistry, cycles/day, DOD, degradation rate, cost per kWh, break-even compensation |

**Tips:**

- After clicking **Apply to Model**, the EV configuration is immediately available in the Studio. Open the system tree to verify that EV categories appear under the EV section.
- The exported YAML snippet can be pasted directly into the `ev_config:` section of a ESFEX system configuration file.
- Use **Export All** to create a complete record of the analysis for documentation and reproducibility.


---


## Mathematical Formulations


### Bass Diffusion Model

\[
\frac{dN}{dt} = \left(p + q \frac{N(t)}{M}\right) \left(M - N(t)\right) \tag{EV-1}
\]

| Symbol | Description |
|--------|-------------|
| \(N(t)\) | Cumulative number of EV adopters at time \(t\) |
| \(M\) | Market potential (total fleet size) |
| \(p\) | Coefficient of innovation (external influence). Represents the probability that a non-adopter will adopt due to external factors (advertising, policy). Typical range: 0.01-0.04. |
| \(q\) | Coefficient of imitation (internal influence). Represents the probability that a non-adopter will adopt due to word-of-mouth from existing adopters. Typical range: 0.20-0.50. |

The Bass model [**[53]**](../reference/bibliography.md#ref53) produces an S-shaped adoption curve. The ratio \(q/p\) determines the curve symmetry: higher ratios produce steeper, more right-skewed curves where adoption accelerates rapidly once a critical mass is reached.


### Logistic Adoption Model

\[
N(t) = \frac{K}{1 + e^{-r(t - t_{\text{mid}})}} \tag{EV-2}
\]

| Symbol | Description |
|--------|-------------|
| \(K\) | Carrying capacity (maximum fleet that can be electrified) |
| \(r\) | Growth rate. Higher values produce steeper S-curves. |
| \(t_{\text{mid}}\) | Midpoint year at which \(N = K/2\). The inflection point of the curve. |

In the transport-specific logistic regression variant used by the wizard, the growth rate \(r\) is parameterized as a function of macroeconomic drivers:

\[
r = \beta_0 + \beta_{\text{fuel}} \cdot \Delta_{\text{fuel}} + \beta_{\text{GDP}} \cdot \text{GDP}_{\text{pc}} + \beta_{\text{urban}} \cdot u + \beta_{\text{infra}} \cdot d_{\text{CS}}
\]

where \(\Delta_{\text{fuel}}\) is the fuel cost savings of EVs over ICE, \(\text{GDP}_{\text{pc}}\) is GDP per capita, \(u\) is urbanization rate, and \(d_{\text{CS}}\) is charging station density.


### Total Cost of Ownership

\[
TCO_{\text{EV}} = P_{\text{EV}} + C_{\text{bat}} \cdot E_{\text{bat}} + C_{\text{elec}} \cdot d \cdot \varepsilon - S_{\text{EV}} \tag{EV-3a}
\]

\[
TCO_{\text{ICE}} = P_{\text{ICE}} + C_{\text{fuel}} \cdot d \cdot f - S_{\text{ICE}} + \Delta M \tag{EV-3b}
\]

| Symbol | Description |
|--------|-------------|
| \(P_{\text{EV}}, P_{\text{ICE}}\) | Vehicle purchase price (EV, ICE) |
| \(C_{\text{bat}}\) | Battery cost ($/kWh) |
| \(E_{\text{bat}}\) | Battery capacity (kWh) |
| \(C_{\text{elec}}\) | Electricity price ($/kWh) |
| \(C_{\text{fuel}}\) | Fuel price ($/liter) |
| \(d\) | Average annual driving distance (km) |
| \(\varepsilon\) | EV energy consumption (kWh/km) |
| \(f\) | ICE fuel consumption (liters/km) |
| \(S_{\text{EV}}, S_{\text{ICE}}\) | Government subsidies |
| \(\Delta M\) | Maintenance cost difference (ICE higher) |

The TCO parity year occurs when \(TCO_{\text{EV}} = TCO_{\text{ICE}}\). Adoption is modeled as a logistic function of the TCO gap, with the `price_sensitivity` parameter controlling how rapidly adoption responds to cost advantages.


### Battery Learning Curve

\[
C_{\text{bat}}(y) = C_0 \cdot (1 - \delta)^y \tag{EV-4}
\]

| Symbol | Description |
|--------|-------------|
| \(C_0\) | Initial battery cost ($/kWh) at the base year |
| \(\delta\) | Annual cost decline rate (default: 0.08 = 8%/year) |
| \(y\) | Years since base year |

This exponential decay approximates the experience-curve learning effect observed in battery manufacturing. At 8%/year decline, a $140/kWh pack in 2025 reaches approximately $64/kWh by 2035 and $29/kWh by 2045.


### Daily Energy Demand

\[
E_{\text{day}} = \sum_{c} N_{c}^{\text{EV}} \cdot d_c \cdot \varepsilon_c \tag{EV-5}
\]

| Symbol | Description |
|--------|-------------|
| \(N_c^{\text{EV}}\) | Number of electric vehicles in category \(c\) |
| \(d_c\) | Average daily driving distance for category \(c\) (km) |
| \(\varepsilon_c\) | Energy consumption for category \(c\) (kWh/km) |

The daily energy is distributed across hours according to the charging scenario (uncontrolled, ToU shifted, or optimized).


### V2G Available Power

\[
P_{\text{V2G}}(t) = \sum_{c} N_c^{\text{EV}} \cdot p_c^{\text{V2G}} \cdot f_c^{\text{conn}}(t) \cdot \phi_c \cdot \eta_c^{\text{dis}} \tag{EV-6}
\]

| Symbol | Description |
|--------|-------------|
| \(N_c^{\text{EV}}\) | Number of EVs in category \(c\) |
| \(p_c^{\text{V2G}}\) | V2G discharge power per vehicle in category \(c\) (kW) |
| \(f_c^{\text{conn}}(t)\) | Fraction of fleet connected at hour \(t\) (from connected-time profile) |
| \(\phi_c\) | V2G participation rate for category \(c\) (fraction of fleet enrolled in V2G program) |
| \(\eta_c^{\text{dis}}\) | Discharge efficiency (default: 0.90) |

The total V2G energy available per day is bounded by the usable SOC window:

\[
E_{\text{V2G, day}} = \sum_{c} N_c^{\text{EV}} \cdot \phi_c \cdot E_c^{\text{bat}} \cdot (SOC_{\max} - SOC_{\min}) \cdot n_{\text{cycles}} \cdot \eta_c^{\text{dis}}
\]

where \(n_{\text{cycles}}\) is the number of V2G cycles per day and \(E_c^{\text{bat}}\) is the battery capacity.


### Battery Degradation — Cycle Component

\[
L_{\text{cycle}} = \alpha \cdot DOD^{\beta} \cdot N_{\text{cycles}} \tag{EV-7}
\]

| Symbol | Description |
|--------|-------------|
| \(L_{\text{cycle}}\) | Capacity loss due to cycling (fraction) |
| \(\alpha, \beta\) | Chemistry-dependent Wohler curve parameters. NMC: higher \(\alpha\), lower cycle tolerance. LFP: lower \(\alpha\), better cycle life. |
| \(DOD\) | Depth of discharge per cycle: \(SOC_{\max} - SOC_{\min}\) |
| \(N_{\text{cycles}}\) | Number of cycles (V2G cycles/day x 365) |


### Capacity Retention

\[
C(y) = C_0 \cdot \left(1 - L_{\text{calendar}}(y) - L_{\text{cycle}}(y)\right) \tag{EV-8}
\]

| Symbol | Description |
|--------|-------------|
| \(C(y)\) | Remaining battery capacity at year \(y\) |
| \(C_0\) | Initial battery capacity (kWh) |
| \(L_{\text{calendar}}(y)\) | Cumulative capacity loss from calendar aging (time and temperature dependent, typically 1-2%/year) |
| \(L_{\text{cycle}}(y)\) | Cumulative capacity loss from cycling (driving + V2G) |

End-of-life is typically defined as 80% retained capacity (\(C(y)/C_0 = 0.80\)). The degradation cost per kWh of V2G energy is:

\[
c_{\text{deg}} = \frac{L_{\text{V2G-cycle}} \cdot C_0 \cdot C_{\text{replace}}}{E_{\text{V2G, annual}}}
\]

where \(C_{\text{replace}}\) is the battery replacement cost ($/kWh) and \(E_{\text{V2G, annual}}\) is the annual V2G energy throughput.


### Peak Shaving

\[
\Delta P_{\text{peak}} = P_{\text{peak, no V2G}} - P_{\text{peak, with V2G}} \tag{EV-9}
\]

| Symbol | Description |
|--------|-------------|
| \(P_{\text{peak, no V2G}}\) | Peak system demand with EV charging but without V2G dispatch |
| \(P_{\text{peak, with V2G}}\) | Peak system demand with V2G actively discharging during peak hours |

Peak shaving is one of the highest-value grid services that V2G can provide, as it reduces the need for expensive peaking generation capacity and can defer transmission/distribution infrastructure upgrades [**[51]**](../reference/bibliography.md#ref51).


---


## Scripting

All wizard computations are available as Python functions through the `evrex` library for batch processing and Jupyter notebooks:

```python
from evrex import (
    EVMacroData,
    TransportContext,
    run_ev_bass_diffusion,
    run_ev_logistic_adoption,
    run_ev_tco_parity,
    run_ev_policy_driven,
    generate_all_scenarios,
    compute_v2g_potential,
    compute_battery_degradation,
    assess_grid_impact,
    fit_adoption_to_ev_config,
)

# Step 1: Define transport context
transport = TransportContext(
    fleet_by_category={"light": 1000, "medium": 200, "heavy": 50, "buses": 30},
    avg_daily_km={"light": 40, "medium": 80, "heavy": 150, "buses": 200},
    energy_consumption={"light": 18, "medium": 25, "heavy": 55, "buses": 80},
    charging_stations=42,
    road_density_km2=3.2,
)

# Step 2: Define macro data
macro = EVMacroData(
    country_iso="CUB",
    gdp_per_capita=5000,
    urbanization_pct=75,
    population=1_000_000,
    inflation_rate=0.03,
    gdp_growth_rate=0.03,
    ev_price={"light": 35000, "medium": 55000, "heavy": 120000, "buses": 300000},
    ice_price={"light": 25000, "medium": 40000, "heavy": 90000, "buses": 250000},
    battery_cost_per_kwh=140,
    battery_cost_decline_rate=0.08,
    fuel_price_gasoline=1.20,
    fuel_price_diesel=1.10,
    electricity_tariff=0.15,
    maintenance_diff_annual=500,
    ice_phaseout_year=2040,
    ev_subsidy_pct=0.10,
    registration_tax_diff=0,
    emission_target_pct=0,
)

# Step 3: Run adoption models
bass = run_ev_bass_diffusion(transport, 2025, 2050, p=0.02, q=0.40)
logistic = run_ev_logistic_adoption(macro, transport, 2025, 2050)
tco = run_ev_tco_parity(macro, transport, 2025, 2050, price_sensitivity=8.0)
policy = run_ev_policy_driven(macro, transport, 2025, 2050)

print(f"Bass: {bass.penetration[-1]*100:.1f}% by {bass.years[-1]}")
print(f"TCO:  {tco.penetration[-1]*100:.1f}% by {tco.years[-1]}")

# Step 5: Select preferred scenario
selected = bass

# Step 6: Generate charging profiles
ev_params = {
    "light":  {"charging_power": 7,  "battery_capacity": 50},
    "medium": {"charging_power": 11, "battery_capacity": 75},
    "heavy":  {"charging_power": 22, "battery_capacity": 150},
    "buses":  {"charging_power": 50, "battery_capacity": 300},
}
fleet_2035 = {cat: selected.fleet_by_category[cat][10] for cat in selected.fleet_by_category}
scenarios = generate_all_scenarios(
    fleet_by_category=fleet_2035,
    ev_categories=ev_params,
    smart_charging_fraction=0.50,
)

for name, s in scenarios.items():
    print(f"  {name}: peak={s.peak_demand_mw:.1f} MW, daily={s.daily_energy_mwh:.1f} MWh")

# Step 7: V2G potential
v2g = compute_v2g_potential(
    fleet_by_category=fleet_2035,
    ev_categories=ev_params,
    v2g_min_soc=0.30,
    v2g_max_soc=0.90,
)
deg = compute_battery_degradation(
    v2g_cycles_per_day=0.5,
    battery_capacity_kwh=50,
    depth_of_discharge=0.60,
    chemistry="LFP",
)
print(f"V2G: {v2g.annual_v2g_potential_gwh:.2f} GWh/year")
print(f"Degradation: {deg.total_degradation_pct_per_year:.2f}%/year")
print(f"Break-even: ${deg.breakeven_compensation:.1f}/MWh")

# Step 8: Grid impact
impact = assess_grid_impact(
    base_demand_24h=[200 + 80*i/24 for i in range(24)],  # simplified
    ev_charging_24h=scenarios["optimized"].aggregate_hourly_mw,
    v2g_potential=v2g,
    v2g_compensation_per_mwh=50,
)
print(f"Peak shaving: {impact.peak_shaving_mw:.1f} MW")
print(f"Net V2G value: ${impact.net_v2g_value:,.0f}")

# Step 9: Generate ESFEX configuration
ev_config = fit_adoption_to_ev_config(
    curve=selected,
    transport=transport,
    num_nodes=1,
)
```

See the [evrex library documentation](https://github.com/your-org/evrex) for full parameter documentation and additional analysis functions.
