# Rooftop Solar Assessment

Distributed rooftop photovoltaic potential analysis and adoption modeling for building stocks. Access via **Workflows > Rooftop Solar Assessment**.

The wizard evaluates rooftop PV deployment at the individual building level, combining geospatial building footprint data, solar resource databases, and panel performance models to compute per-building and aggregated PV potential. It then projects technology adoption over time using four complementary diffusion models, and feeds the selected adoption trajectory into the ESFEX optimizer as a time-varying virtual generator. The methodology follows rooftop suitability assessment practices [**[55]**](../reference/bibliography.md#ref55) and distributed generation adoption modeling [**[54]**](../reference/bibliography.md#ref54), with cost assumptions from [**[48]**](../reference/bibliography.md#ref48) and diffusion dynamics from [**[53]**](../reference/bibliography.md#ref53).

The analysis engine is provided by the `rooftex` library for building-level PV assessment and the `esfex.models.adoption_models` module for adoption curve generation.

The wizard is organized in two phases:

- **Phase A (Steps 1-5)**: Rooftop Potential. Define the geographic domain, fetch building and solar data, configure panel and roof parameters, run the per-building analysis, and review results.
- **Phase B (Steps 6-9)**: Adoption Modeling & Integration. Gather macroeconomic data, run four adoption models (logistic, Bass, techno-economic, agent-based), compare scenarios, and apply the selected curve to the ESFEX model.


---


## Phase A -- Rooftop Potential Assessment


### Step 1: Domain Definition

Define the geographic bounding box that covers the area of interest.

**Two input methods:**

- **Draw on map**: Click "Draw Rectangle", then click and drag directly on the map. The wizard minimizes itself so the full map is visible during drawing. Once the rectangle is completed, the wizard restores itself and displays the captured coordinates.
- **Manual entry**: Enter South latitude, North latitude, West longitude, and East longitude directly in the coordinate spinboxes (6 decimal places). Click "Apply" to validate.

The wizard displays the approximate domain area in km squared, computed using spherical distance:

\[
A \approx ((\phi_N - \phi_S) \times 111.32) \times ((\lambda_E - \lambda_W) \times 111.32 \times \cos\bar{\phi})
\]

where \(\bar{\phi} = (\phi_S + \phi_N)/2\) is the midpoint latitude.

**Show on Map**: After coordinates are set, click to visualize the domain rectangle on the map and fit the view to its extent.

**Tips:**

- The domain should cover the urban or peri-urban area where rooftop PV deployment is being assessed. Larger domains require longer data fetching and analysis times.
- North must be greater than South, and East must be greater than West.


### Step 2: Data Sources

Fetch building footprint and solar resource data from external databases. Both datasets must be successfully loaded before proceeding to the next step.

**Building footprints:**

| Source | Description |
|--------|-------------|
| **Overture Maps** | Open dataset with global building footprints. Best coverage in urban areas. |
| **Microsoft ML Buildings** | Machine learning-derived footprints from satellite imagery. Over 1.2 billion buildings worldwide. |
| **Google Open Buildings** | Google's ML-derived dataset. Best coverage in Africa, South/Southeast Asia, and Latin America. |

Click **Fetch Buildings** to download building footprints within the domain. The operation runs in a background thread with a progress bar. The status displays the total number of buildings retrieved and the fraction with height data (needed for shading analysis). Buildings with height information enable more accurate inter-building shading estimation.

**Solar resource:**

| Source | API Key Required | Description |
|--------|-----------------|-------------|
| **PVGIS** | No | EU Joint Research Centre Photovoltaic Geographical Information System. Covers Europe, Africa, and parts of Asia. |
| **NSRDB** | Yes | NREL National Solar Radiation Database. Covers the Americas and parts of South Asia. |

Select a reference year (2005-2024, default 2022) and click **Fetch Solar Data** to download hourly solar resource data (GHI, DNI, DHI) for the domain center. When NSRDB is selected, an API key text field appears -- enter your NREL API key (available at developer.nrel.gov).

**Tips:**

- Overture Maps provides the most consistent global coverage. Use Microsoft ML Buildings when Overture has sparse data in your region.
- PVGIS does not require authentication and is the easiest option for European, African, and Asian locations. Use NSRDB for the Americas.
- The reference year should be a recent year with typical weather conditions. Avoid years with unusual cloud patterns or extreme events.


### Step 3: Panel & Roof Configuration

Configure PV panel specifications, roof suitability criteria, and shading analysis parameters. All fields have sensible defaults and can be adjusted to reflect available module technology and local building characteristics.

**Panel specifications:**

| Parameter | Range | Default | Unit | Description |
|-----------|-------|---------|------|-------------|
| Module efficiency | 5-50% | 21% | fraction | PV cell conversion efficiency under standard test conditions. Commercial crystalline silicon modules are typically 17-23%. |
| Module power | 100-800 | 400 | W | Nominal power output per module under STC. |
| Module area | 0.5-5.0 | 2.0 | m squared | Physical area of one PV module. |
| Performance ratio | 50-99% | 80% | fraction | System-level derating factor accounting for inverter losses, wiring, soiling, and thermal effects. Rooftop systems are typically 75-85% [**[48]**](../reference/bibliography.md#ref48). |
| System losses | 0-50% | 14% | fraction | Additional losses from mismatch, aging, and availability. Combined with PR for total derate. |

**Roof suitability:**

| Parameter | Range | Default | Unit | Description |
|-----------|-------|---------|------|-------------|
| Suitable fraction | 5-100% | 30% | fraction | Fraction of total roof area usable for PV panels, accounting for obstructions, setbacks, vents, and unfavorable orientations [**[55]**](../reference/bibliography.md#ref55). Typical range: 20-50%. |
| Min building area | 1-500 | 20 | m squared | Buildings with footprint area smaller than this threshold are excluded as too small for viable PV installation. |
| Default tilt | 0-60 | 0 (auto) | degrees | Panel tilt angle. When set to 0, the wizard auto-computes the optimal tilt as the absolute value of the site latitude (rule of thumb for annual yield maximization). |
| Default azimuth | 0-360 | 180 | degrees | Panel azimuth. 180 degrees is south-facing (optimal in the northern hemisphere). The wizard automatically adjusts to 0 degrees (north-facing) for southern hemisphere locations. |

**Shading analysis:**

| Parameter | Range | Default | Unit | Description |
|-----------|-------|---------|------|-------------|
| Enable shading | -- | On | -- | When enabled, the analysis computes inter-building shading losses using building heights and solar position throughout the year. Requires buildings with height data. |
| Search radius | 10-500 | 50 | m | Maximum distance to consider neighboring buildings as potential shadow casters. Larger radii improve accuracy but increase computation time. |

**Tips:**

- For flat commercial rooftops, increase the suitable fraction to 40-50%. For pitched residential roofs, 20-30% is more realistic.
- Setting default tilt to 0 (auto) provides a good estimate. Override for specific installation scenarios such as flat-roof ballasted systems (typically 10-15 degrees).
- Disable shading analysis for rapid preliminary assessments or when building height data is unavailable.


### Step 4: Analysis

Click **Run Analysis** to execute the rooftop PV assessment pipeline. The wizard displays a configuration summary before starting, showing the number of buildings, solar data source, panel parameters, and roof criteria.

**Analysis pipeline:**

1. **Solar position computation**: Computes hourly solar zenith and azimuth angles for the site using pvlib.
2. **Plane-of-array irradiance**: Decomposes global, direct, and diffuse irradiance onto the tilted module surface using the isotropic transposition model.
3. **Building filtering**: Excludes buildings with usable roof area (footprint area times suitable fraction) below the minimum area threshold.
4. **Module fitting**: Calculates how many modules fit on each building's usable roof area: \(n = A_{\text{usable}} / A_{\text{module}}\).
5. **Shading analysis**: (If enabled) For each building, identifies taller neighbors within the search radius. Samples 36 representative solar positions across the year and estimates shadow reach using simplified geometry (see [RSP-8](#shading-loss-factor-rsp-8)). The shading factor reduces the building's annual yield proportionally.
6. **Per-building yield**: Computes annual energy yield per building as the product of POA irradiance, module efficiency, usable area, performance ratio, system loss factor, and shading factor (see [RSP-2](#building-pv-capacity-rsp-2)).
7. **Aggregation**: Sums per-building results into total capacity (kWp), annual yield (MWh/yr), average capacity factor, and average specific yield.

The analysis runs on a background thread with a progress bar and real-time log output. A **Cancel** button is available for interruption. Upon completion, the log displays summary statistics.

**Tips:**

- For domains with thousands of buildings, the analysis may take several minutes. The progress bar updates every 100 buildings.
- Buildings without height data still receive PV capacity estimates but are excluded from the shading calculation.


### Step 5: Results

Summary statistics and export options for the completed analysis.

**Summary statistics:**

| Metric | Description |
|--------|-------------|
| Total Buildings | Number of buildings in the domain |
| Suitable Buildings | Number of buildings meeting the minimum area and suitability criteria |
| Total Usable Roof Area | Aggregated usable roof area across all suitable buildings (m squared) |
| Total Installed Capacity | Aggregated PV nameplate capacity (kWp) |
| Annual Energy Yield | Total expected annual energy production (MWh/yr) |
| Average Capacity Factor | Mean ratio of actual output to nameplate capacity across all suitable buildings (%) |
| Average Specific Yield | Mean energy production per unit capacity (kWh/kWp/yr). Typical range: 1,000-1,800 depending on location and climate. |

**Actions:**

| Action | Description |
|--------|-------------|
| **Show on Map** | Display suitable buildings on the map as a color-coded GeoJSON overlay. Per-building capacity (kW), annual yield (kWh), specific yield, usable roof area, and shading loss are accessible by hovering. Buildings are reprojected to WGS84 for display. |
| **Export GeoJSON** | Save the full building-level results as a georeferenced GeoJSON file, including geometry, capacity, yield, specific yield, roof area, shading loss, and suitability flag. |
| **Export CSV** | Save a tabular summary with one row per building: building_id, usable_roof_area_m2, capacity_kw, annual_kwh, specific_yield_kwh_kwp, shading_loss, suitable. |


---


## Phase B -- Adoption Modeling & Integration


### Step 6: Macroeconomic Data

Fetch and configure macroeconomic parameters that drive the adoption models. Data can be auto-fetched by country or entered manually.

**Country detection:**

Click **Detect** to auto-identify the country from the domain bounding box (reverse geocoding). Alternatively, enter the ISO-3 country code directly (e.g., CUB, USA, DEU).

**Auto-fetch**: Click **Fetch All** to query three data sources in parallel background threads:

| Source | Data Retrieved |
|--------|---------------|
| **World Bank** | GDP per capita (USD), urbanization rate (%), population |
| **IMF** | GDP growth rate, inflation rate |
| **IRENA** | PV system cost ($/kW), cost learning trajectory [**[48]**](../reference/bibliography.md#ref48) |

Fetched values populate the form fields automatically. If any source fails, partial results are still applied and the status indicates which sources encountered errors.

**Editable parameters:**

| Parameter | Range | Default | Unit | Description |
|-----------|-------|---------|------|-------------|
| GDP per capita | 100-200,000 | 5,000 | USD | Gross domestic product per capita. Higher GDP correlates with greater rooftop PV adoption. |
| Electricity tariff | 0.001-2.000 | 0.15 | $/kWh | Retail electricity price. Higher tariffs improve PV economic attractiveness. |
| PV system cost | 100-10,000 | 1,200 | $/kW | Current installed cost of rooftop PV (modules + inverter + BOS + installation). |
| Learning rate | 1-50% | 20% | fraction | Cost reduction per cumulative capacity doubling. Historical solar PV learning rate is approximately 20% [**[48]**](../reference/bibliography.md#ref48). |
| Urbanization | 0-100 | 75 | % | Urban population fraction. Affects the density of suitable rooftops. |
| Population | 1,000-2B | 1,000,000 | -- | Total population in the analysis region. |
| Discount rate | 1-50% | 8% | fraction | Real discount rate for economic calculations. |
| Inflation rate | -5% to 50% | 3% | fraction | Annual consumer price inflation rate. |
| GDP growth rate | -10% to 20% | 3% | fraction | Annual real GDP growth rate for macro-variable projections. |

**Tips:**

- Start with auto-fetched values and adjust to reflect local conditions. Official statistics may lag reality by 1-2 years.
- The electricity tariff is the single most influential parameter on adoption speed. Use the effective residential tariff (including taxes and subsidies).
- PV system cost should include all balance-of-system costs, not just module price.


### Step 7: Adoption Models

Configure and run four adoption modeling methods. Each method projects year-by-year rooftop PV penetration from a base year to a target year, bounded by the technical maximum capacity computed in Phase A.

**Time horizon:**

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| Base year | 2020-2040 | 2025 | Start year for the adoption projection. |
| Target year | 2030-2080 | 2050 | End year for the adoption projection. |

**Method selection**: Enable or disable each of the four methods via checkboxes (all enabled by default):

1. **Logistic regression** -- Macro-economic drivers determine adoption probability through a logistic function of GDP, tariff, PV cost, and urbanization (see [RSP-4](#logistic-adoption-rsp-4)).
2. **Bass diffusion** -- Classic innovation/imitation model [**[53]**](../reference/bibliography.md#ref53) with external influence (innovation coefficient \(p\)) and internal influence (imitation coefficient \(q\)) (see [RSP-6](#bass-diffusion-rsp-6)).
3. **Techno-economic** -- Adoption as a sigmoid function of the gap between the electricity tariff and the levelized cost of rooftop PV, updated yearly as PV costs decline (see [RSP-5](#techno-economic-adoption-rsp-5)).
4. **Agent-based model (ABM)** -- Heterogeneous household agents with income-dependent discount rates, spatial neighbor effects, and stochastic awareness growth [**[54]**](../reference/bibliography.md#ref54) (see [RSP-7](#abm-adoption-probability-rsp-7)).

**Presets**: A preset selector provides three parameter configurations:

| Preset | Logistic | Bass (p, q) | Techno-Econ | ABM |
|--------|----------|-------------|-------------|-----|
| Conservative | beta_0=-4.0, beta_policy=0.2 | p=0.02, q=0.25 | sensitivity=10 | w_econ=0.6, threshold=0.6 |
| Moderate | defaults | p=0.03, q=0.38 | sensitivity=15 | defaults |
| Aggressive | beta_0=-2.0, beta_policy=0.8 | p=0.05, q=0.50 | sensitivity=20 | w_econ=0.4, threshold=0.4 |

**Validation data**: Optional historical data for model calibration and visual comparison. Three input methods:

| Source | Description |
|--------|-------------|
| **Fetch IRENA** | Download historical installed solar capacity for the detected country from IRENA statistics. |
| **Import CSV** | Load a CSV file with columns `year` and `capacity_mw`. |
| **Manual input** | Open a dialog with a 10-row table for typing year/capacity pairs. |

Click **Run Models** to execute all selected methods in a background thread. The log displays each method as it runs and the final installed capacity at the target year.

**Tips:**

- Run all four methods for an initial comparison, then focus on the 1-2 methods that best match validation data.
- The Bass diffusion model is well-suited when historical data is available for calibrating \(p\) and \(q\). The ABM is best when spatial clustering of adoption is important.
- Use Conservative presets for risk-averse planning and Aggressive presets for optimistic scenarios.


### Step 8: Scenario Comparison

Compare adoption curves from all computed methods on a single chart and select one for integration.

**Adoption chart:**

A matplotlib line chart plots installed capacity (MW) versus year for each method, using distinct colors. The ABM curve includes a shaded confidence band (10th-90th percentile across stochastic iterations). If validation data was loaded in Step 7, observed data points are overlaid as scatter markers for visual comparison.

**Curve selection:**

Radio buttons list each method with its final capacity at the target year. Select the method that best represents the expected adoption trajectory. The selection determines which curve is applied to the ESFEX model in Step 9.

**Summary table:**

A table displays installed capacity for each method at every 5th year and the final year, enabling quick numerical comparison across scenarios.

**Export options:**

| Format | Content |
|--------|---------|
| **Export PNG** | Save the comparison chart as a 150 DPI PNG image. |
| **Export CSV** | Save all curves as a CSV file with columns: year, method_penetration, method_capacity_mw (for each method). |


### Step 9: Grid Integration

Apply the selected adoption curve to the ESFEX model or export analysis results for external use.

**Selected curve summary**: Displays the chosen method name, projection period, final penetration fraction, and final installed capacity (MW).

**Option A -- Apply to ESFEX Model:**

Click **Apply to Model** to write adoption parameters into the GUI model's rooftop solar configuration. The wizard translates the adoption curve into the `RooftopSolarConfig` structure by:

1. Extracting initial adoption (penetration at base year) and maximum adoption (penetration at target year, with low/medium/high scenarios at 0.6x, 1.0x, and 1.3x of the final value).
2. Estimating the adoption rate from the slope at the curve midpoint.
3. Computing per-node system counts and average system sizes from the Phase A building analysis.
4. Setting system cost, performance ratio, and degradation rate from the macroeconomic and panel configuration data.
5. Enabling rooftop solar simulation in the system settings.

The applied configuration creates a time-varying virtual generator in the ESFEX optimization model, where the available capacity at each planning year follows the selected adoption trajectory.

**Option B -- Export Files:**

| Export | Format | Content |
|--------|--------|---------|
| **Adoption Curves** | CSV | All methods: year, penetration fraction, installed capacity (MW) per method. |
| **Macro Data** | JSON | Full macroeconomic data snapshot (GDP, tariff, costs, rates). |
| **Model Parameters** | JSON | Per-method fitted parameters, final penetration, and final capacity. |
| **Buildings Analysis** | CSV | Per-building results from Phase A: building_id, capacity_kw, annual_kwh, specific_yield, usable_roof_area, suitable. |


---


## Mathematical Formulations


### Specific Yield (RSP-1)

\[
Y_{\text{sp}} = \frac{E_{\text{annual}}}{P_{\text{peak}}} \tag{RSP-1}
\]

| Symbol | Unit | Description |
|--------|------|-------------|
| \(Y_{\text{sp}}\) | kWh/kWp/yr | Annual energy production per unit of installed peak capacity |
| \(E_{\text{annual}}\) | kWh/yr | Total annual energy yield of the PV system |
| \(P_{\text{peak}}\) | kWp | Nameplate (peak) capacity under standard test conditions |

Specific yield is the primary metric for comparing solar resource quality across locations. Values typically range from 1,000 kWh/kWp/yr in northern Europe to 1,800+ kWh/kWp/yr in sunbelt regions.


### Building PV Capacity (RSP-2)

\[
P_b = \left\lfloor \frac{A_{\text{roof}} \times f_{\text{suitable}}}{A_{\text{module}}} \right\rfloor \times P_{\text{module}} \tag{RSP-2}
\]

| Symbol | Unit | Description |
|--------|------|-------------|
| \(P_b\) | kW | PV capacity installable on building \(b\) |
| \(A_{\text{roof}}\) | m squared | Building footprint area |
| \(f_{\text{suitable}}\) | -- | Fraction of roof area usable for PV (0-1). Accounts for setbacks, vents, HVAC, skylights, and unfavorable orientations [**[55]**](../reference/bibliography.md#ref55). |
| \(A_{\text{module}}\) | m squared | Physical area of one PV module |
| \(P_{\text{module}}\) | kW | Rated power of one PV module |

The suitable fraction \(f_{\text{suitable}}\) is the key parameter governing how much of a building's total roof area can actually host PV panels. Studies report values of 0.22-0.50 depending on building type, with 0.30 as a reasonable central estimate for mixed urban building stocks [**[55]**](../reference/bibliography.md#ref55).


### Annual Energy Yield (RSP-3)

\[
E_b = I_{\text{POA}} \times \eta_{\text{module}} \times A_{\text{usable}} \times PR \times (1 - L_{\text{sys}}) \times (1 - L_{\text{shade}}) \tag{RSP-3}
\]

| Symbol | Unit | Description |
|--------|------|-------------|
| \(E_b\) | kWh/yr | Annual energy yield of building \(b\) |
| \(I_{\text{POA}}\) | kWh/m squared/yr | Annual plane-of-array irradiation (sum of hourly values) |
| \(\eta_{\text{module}}\) | -- | Module conversion efficiency (fraction) |
| \(A_{\text{usable}}\) | m squared | Usable roof area: \(A_{\text{roof}} \times f_{\text{suitable}}\) |
| \(PR\) | -- | Performance ratio: system-level derating for inverter, wiring, thermal, and soiling losses |
| \(L_{\text{sys}}\) | -- | System losses fraction (mismatch, aging, availability) |
| \(L_{\text{shade}}\) | -- | Shading loss fraction from inter-building shadows (see [RSP-8](#shading-loss-factor-rsp-8)) |

The performance ratio \(PR\) captures the gap between ideal module output and real system output. It is the most commonly used quality metric for PV installations, with values of 0.75-0.85 typical for well-designed rooftop systems [**[48]**](../reference/bibliography.md#ref48).


### Logistic Adoption (RSP-4)

\[
N(t) = \frac{K}{1 + e^{-z(t)}} \tag{RSP-4}
\]

where

\[
z(t) = \beta_0 + \beta_{\text{GDP}} \cdot GDP(t) + \beta_{\text{tariff}} \cdot \tau(t) + \beta_{\text{cost}} \cdot C_{\text{PV}}(t) + \beta_{\text{urban}} \cdot U + \beta_{\text{policy}}
\]

| Symbol | Default | Description |
|--------|---------|-------------|
| \(K\) | from Phase A | Technical maximum rooftop capacity (MW) |
| \(\beta_0\) | -3.0 | Intercept (baseline propensity) |
| \(\beta_{\text{GDP}}\) | 0.00005 | GDP per capita coefficient (higher GDP promotes adoption) |
| \(\beta_{\text{tariff}}\) | 8.0 | Electricity tariff coefficient (higher tariff promotes adoption) |
| \(\beta_{\text{cost}}\) | -0.001 | PV system cost coefficient (higher cost discourages adoption) |
| \(\beta_{\text{urban}}\) | 0.02 | Urbanization coefficient |
| \(\beta_{\text{policy}}\) | 0.5 | Policy incentive factor |

Macro-economic variables are projected forward at each year: GDP grows at the configured growth rate, tariffs escalate at half the inflation rate, and PV cost declines at approximately 4% per year (or follows the IRENA trajectory if available).


### Techno-Economic Adoption (RSP-5)

\[
N(t) = \frac{K}{1 + e^{-\alpha \cdot (\tau(t) - LCOE(t))}} \tag{RSP-5}
\]

where

\[
LCOE(t) = \frac{C_{\text{PV}}(t) \times CRF(r, n)}{Y_{\text{sp}} \times (1 - \delta \cdot n/2)} \tag{RSP-5a}
\]

\[
CRF(r, n) = \frac{r(1+r)^n}{(1+r)^n - 1} \tag{RSP-5b}
\]

| Symbol | Default | Unit | Description |
|--------|---------|------|-------------|
| \(\alpha\) | 15.0 | 1/($/kWh) | Price sensitivity: steepness of the sigmoid response to the tariff-LCOE gap |
| \(\tau(t)\) | -- | $/kWh | Retail electricity tariff at year \(t\) |
| \(C_{\text{PV}}(t)\) | -- | $/kW | PV system cost at year \(t\) |
| \(r\) | 0.08 | -- | Discount rate |
| \(n\) | 25 | years | System lifetime |
| \(Y_{\text{sp}}\) | -- | kWh/kWp/yr | Specific yield (irradiance times performance ratio) |
| \(\delta\) | 0.005 | 1/yr | Annual panel degradation rate |

Adoption follows a sigmoid function of the gap between the electricity tariff and the PV levelized cost. As PV costs decline over time (learning curve), the gap widens and adoption accelerates. When \(\tau > LCOE\), rooftop PV is economically attractive, driving the sigmoid past 50% penetration.


### Bass Diffusion (RSP-6)

\[
F(t) = \frac{1 - e^{-(p+q)t}}{1 + \frac{q}{p} \cdot e^{-(p+q)t}} \tag{RSP-6}
\]

\[
N(t) = K \cdot F(t) \tag{RSP-6a}
\]

| Symbol | Default | Description |
|--------|---------|-------------|
| \(p\) | 0.03 | Innovation coefficient (external influence: advertising, policy, media). Typical range for energy technologies: 0.01-0.05. |
| \(q\) | 0.38 | Imitation coefficient (internal influence: word-of-mouth, neighbor effects). Typical range: 0.20-0.50 [**[53]**](../reference/bibliography.md#ref53). |
| \(K\) | from Phase A | Market potential (maximum capacity, MW) |

The Bass model [**[53]**](../reference/bibliography.md#ref53) captures the classic S-curve dynamics of technology diffusion: early adoption driven by innovators (coefficient \(p\)), followed by acceleration as imitators adopt through social influence (coefficient \(q\)). The ratio \(q/p\) determines the asymmetry of the S-curve. If an initial penetration is specified (e.g., 1%), the model solves for the time offset \(t_0\) such that \(F(t_0)\) matches the initial value.


### ABM Adoption Probability (RSP-7)

Each agent \(a\) evaluates a weighted utility function at each time step:

\[
U_a(t) = w_{\text{econ}} \cdot U_a^{\text{econ}}(t) + w_{\text{social}} \cdot U_a^{\text{social}}(t) + w_{\text{aware}} \cdot U_a^{\text{aware}}(t) \tag{RSP-7}
\]

Agent \(a\) adopts when \(U_a(t) > \theta_a\), where \(\theta_a\) is a stochastic threshold drawn from \(\mathcal{N}(\theta, 0.05)\).

**Economic component:**

\[
U_a^{\text{econ}}(t) = \frac{1}{1 + e^{-10 \cdot (\tau(t) - LCOE_a(t))}} \tag{RSP-7a}
\]

where \(LCOE_a\) uses the agent's personal discount rate, derived from income: \(r_a = r \cdot \bar{I} / I_a\), clipped to [0.02, 0.30]. Lower-income agents have higher personal discount rates, reflecting limited access to capital.

**Social component:**

\[
U_a^{\text{social}}(t) = \frac{\text{adopted neighbors within radius}}{\text{total neighbors within radius}} \tag{RSP-7b}
\]

Neighbor proximity is computed using a kd-tree spatial index. The neighbor radius (default 1 km) determines the spatial range of peer influence.

**Awareness component:**

\[
U_a^{\text{aware}}(t) = \frac{1}{1 + e^{-0.3(t - 10)}} + \epsilon, \quad \epsilon \sim \mathcal{N}(0, 0.1) \tag{RSP-7c}
\]

Awareness follows a logistic growth curve (slow at first, then accelerating), reflecting growing public familiarity with rooftop PV technology over time.

| Parameter | Default | Description |
|-----------|---------|-------------|
| \(w_{\text{econ}}\) | 0.5 | Weight for economic utility |
| \(w_{\text{social}}\) | 0.3 | Weight for social (peer) utility |
| \(w_{\text{aware}}\) | 0.2 | Weight for awareness utility |
| \(\theta\) | 0.5 | Adoption threshold (mean) |
| \(n_{\text{agents}}\) | 1,000 | Number of household agents |
| \(n_{\text{iter}}\) | 20 | Stochastic iterations for confidence bounds |
| Neighbor radius | 1.0 km | Spatial range for peer effects |

Agents' incomes are drawn from \(\mathcal{N}(\bar{I}, 0.5\bar{I})\) clipped to \([0.1\bar{I}, 5\bar{I}]\). The model runs \(n_{\text{iter}}\) iterations; penetration is reported as the mean, with 10th and 90th percentile confidence bounds [**[54]**](../reference/bibliography.md#ref54).


### Shading Loss Factor (RSP-8)

\[
L_{\text{shade}} = \frac{N_{\text{shaded}}}{N_{\text{sampled}}} \tag{RSP-8}
\]

where \(N_{\text{sampled}} = 36\) representative solar positions (12 months times 3 hours per day) are evaluated. For each sample, shadow geometry is computed:

\[
S_{\text{length}} = \frac{h_{\text{neighbor}} - h_{\text{building}}}{\tan(\alpha_{\text{solar}})} \tag{RSP-8a}
\]

| Symbol | Description |
|--------|-------------|
| \(L_{\text{shade}}\) | Fraction of sampled time steps where the building is shaded (0-1) |
| \(h_{\text{neighbor}}\) | Height of the neighboring (taller) building (m) |
| \(h_{\text{building}}\) | Height of the target building (m) |
| \(\alpha_{\text{solar}}\) | Solar elevation angle (degrees) |
| \(S_{\text{length}}\) | Horizontal shadow length cast by the height difference (m) |

A building is considered shaded at a given time step if a taller neighbor within the search radius casts a shadow whose length exceeds the inter-building distance and whose direction (opposite to the solar azimuth) aligns with the target building (dot product > 0.5). Only buildings taller by at least 1 meter are considered.


---


## Scripting

All wizard computations are available as Python functions for batch processing and Jupyter notebooks:

```python
from esfex.visualization.workflows.solar_analysis import (
    AnalysisConfig,
    SolarRooftopAnalyzer,
)
from esfex.models.adoption_models import (
    MacroeconomicData,
    run_logistic_adoption,
    run_bass_diffusion,
    run_techno_economic,
    run_abm_adoption,
    fit_adoption_to_rooftop_config,
)

# Phase A: Rooftop potential (requires buildings GeoDataFrame and solar data)
config = AnalysisConfig(
    module_efficiency=0.21,
    module_power_w=400,
    module_area_m2=2.0,
    performance_ratio=0.80,
    system_losses=0.14,
    suitable_fraction=0.30,
    min_building_area_m2=20.0,
    enable_shading=True,
    shading_search_radius_m=50.0,
)

# Phase B: Adoption modeling
macro = MacroeconomicData(
    country_iso="CUB",
    gdp_per_capita=9500.0,
    electricity_tariff=0.12,
    pv_system_cost=1100.0,
    urbanization_pct=77.0,
    population=11_000_000,
    discount_rate=0.08,
)

max_mw = 150.0  # from Phase A analysis summary

logistic = run_logistic_adoption(macro, max_mw, base_year=2025, target_year=2050)
bass = run_bass_diffusion(max_mw, base_year=2025, target_year=2050, p=0.03, q=0.38)
techno = run_techno_economic(macro, max_mw, avg_irradiance_kwh_m2=1700.0)
abm = run_abm_adoption(macro, max_mw, n_agents=2000, n_iterations=30)

for curve in [logistic, bass, techno, abm]:
    print(f"{curve.method}: {curve.capacity_mw[-1]:.1f} MW by {curve.years[-1]}")

# Integration: convert curve to ESFEX rooftop config
config_dict = fit_adoption_to_rooftop_config(
    curve=bass,
    macro=macro,
    num_nodes=1,
    systems_per_node=[5000],
    avg_system_size=[5.2],
)
print(f"Adoption rate (medium): {config_dict['adoption_rates']['medium']:.3f}")
```

See the [Adoption Models API](../api/models-adoption-models.md) and [Solar Analysis API](../api/visualization-solar-analysis.md) for full parameter documentation.
