# Wind Assessment

Wind resource assessment and advanced analysis wizard. Access via **Workflows > Wind Assessment**.

The wizard evaluates onshore and offshore wind energy potential over a user-defined geographic domain. It downloads ERA5 reanalysis wind data (or MERRA-2 via NASA POWER), fits Weibull distributions, applies turbine power curves from a comprehensive database, runs multi-criteria suitability analysis, and generates development zone maps. The advanced analysis phase adds detailed wind characterization, project-level financial evaluation, inter-turbine wake modeling, and hourly availability profile generation for the ESFEX optimization engine. Wind resource modeling is provided by the `windrex` library.

The wizard is organized in two phases:

- **Phase A (Steps 1-5)**: Resource Assessment. Define the analysis domain, select a turbine model and configure assessment parameters, set MCDA criteria and weights, run the spatial analysis, and review results with development zone generation.
- **Phase B (Steps 6-9)**: Advanced Analysis. Characterize the wind regime (Weibull fit, wind rose, diurnal and seasonal patterns), evaluate project financials (LCOE, NPV, IRR, sensitivity), model wake effects (Jensen/Park, array efficiency, spacing optimization), and generate hourly capacity factor profiles for system generators.

All wind-specific computations use the `windrex` library, which can also be used independently for scripting and batch analysis -- see [Scripting](#scripting).


---


## Phase A -- Resource Assessment


### Step 1: Domain Definition

Define the geographic bounding box for the wind resource assessment. The domain determines the spatial extent over which wind data is fetched and analyzed.

**Draw on map:**

Click **Draw Rectangle** to interactively draw the analysis domain on the map. The wizard minimizes while you draw, then restores automatically once the rectangle is placed. The bounding box coordinates are populated in the manual coordinate fields.

**Manual coordinates:**

Enter the south/north latitude and west/east longitude boundaries directly. Click **Apply Coordinates** to set the domain, then **Show on Map** to visualize it.

Once a domain is set, the approximate area in km^2 is displayed. For offshore wind, extend the domain to cover the maritime area of interest.

**Tips:**

- A typical onshore domain for a single wind farm site is 20-50 km on a side. Larger domains (100+ km) are appropriate for regional screening studies.
- The domain must have north > south and east > west. The wizard validates this before proceeding.


### Step 2: Turbine & Assessment Configuration

Select a turbine model from a comprehensive database and configure the assessment parameters.

**Turbine selection:**

The wizard loads the built-in atlite turbine database on a background thread when the step is first displayed. An additional 100+ turbine models from the Open Energy Database (OEDB) can be downloaded by clicking **Load OEDB** (requires internet).

The turbine table can be filtered by:

| Filter | Description |
|--------|-------------|
| Manufacturer | Dropdown of all available manufacturers, or "All" |
| Min Power | Minimum rated power in MW |
| Max Power | Maximum rated power in MW |

The table displays the following columns for each turbine:

| Column | Description |
|--------|-------------|
| Manufacturer | Turbine manufacturer name |
| Model | Turbine model designation |
| Rated (MW) | Nameplate capacity |
| Rotor (m) | Rotor diameter |
| Hub (m) | Default hub height |
| Source | Data source (atlite built-in or OEDB) |

Selecting a turbine displays a detail panel with the full power curve summary, including cut-in speed, rated wind speed, cut-out speed, and a compact ASCII power curve visualization at key wind speeds (3, 5, 7, 9, 11, 13, 15, 20, 25 m/s).

**Hub height override:**

The hub height defaults to the selected turbine's specification but can be overridden (range: 30-300 m). Higher hubs access stronger, more consistent winds but increase tower cost.

**Analysis parameters:**

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| Analysis Year | 2022 | 1979-2023 | Calendar year for ERA5 reanalysis data |
| Grid Resolution | 0.25 deg | 0.05-2.0 deg | Spatial grid spacing. Finer resolution increases accuracy but also download time and computation |
| Installation Type | Onshore | Onshore / Offshore | Determines terrain assumptions and cost parameters |
| Data Source | Open-Meteo | Open-Meteo / NASA POWER / ERA5 via atlite | Wind data provider. Open-Meteo uses ERA5 data with fast download. NASA POWER uses MERRA-2 reanalysis. ERA5 via atlite provides the most control but requires a CDS API key and can take hours |
| Parallel Workers | Auto | 0-64 | Number of parallel threads for data fetching and computation. 0 (Auto) uses all available cores |

**Zone criteria:**

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| Min Capacity Factor | 0.25 | 0.05-0.80 | Minimum CF for a site to be considered feasible |
| Zone Buffer | 5.0 km | 1-100 km | Buffer distance around feasible clusters for development zone delineation |

**Tips:**

- For ERA5 via atlite, you need a Copernicus Climate Data Store account. Create the file `~/.cdsapirc` with your API key before running the analysis.
- Open-Meteo provides the fastest downloads for exploratory assessments; switch to ERA5 via atlite for final studies requiring full control over the data pipeline.
- Specific power (W/m^2 of swept area) is shown in the turbine detail panel. Lower specific power turbines (below 300 W/m^2) capture more energy in low-wind regimes.


### Step 3: MCDA Criteria

Configure the multi-criteria decision analysis (MCDA) for evaluating wind development zone suitability.

**Weighting method:**

| Method | Description |
|--------|-------------|
| Manual | Manually assign weights to each criterion. Weights are normalized to sum to 1 |
| Entropy | Weights computed automatically from data using Shannon entropy. Criteria with more spatial variation receive higher weights |
| PCA | Weights derived from first principal component loadings. Criteria that explain the most variance receive higher weights |

**Criteria table:**

| Criterion | Direction | Default Weight | Description |
|-----------|-----------|----------------|-------------|
| Wind Capacity Factor | Maximize | 0.40 | Annual average capacity factor at hub height. Higher values indicate better wind resource |
| Terrain Slope | Minimize | 0.15 | Terrain gradient from DEM. Flat terrain is preferred for turbine foundations and access roads |
| Elevation | Minimize | 0.10 | Terrain elevation above sea level. Extreme elevations increase construction difficulty |
| LULC Suitability | Maximize | 0.20 | Land use / land cover suitability score. Cropland and grassland score high; built-up areas and water bodies score low |
| Distance to Grid | Minimize | 0.15 | Distance to existing transmission infrastructure. Proximity reduces grid connection cost |

Each criterion can be individually enabled or disabled via a checkbox. When using Entropy or PCA weighting, the weight spinboxes are disabled (weights are computed from data).

**Land cover scoring:**

Check **Customize LULC Scores** to expand the land cover scoring table. Each land cover class (ESA WorldCover classification) has a suitability score from 0 (unsuitable) to 1 (ideal). Default scores are provided by `windrex.DEFAULT_LULC_SCORES`:

| Code | Land Cover Class | Default Score |
|------|-----------------|---------------|
| 10 | Tree cover | Low |
| 20 | Shrubland | Medium |
| 30 | Grassland | High |
| 40 | Cropland | High |
| 50 | Built-up | Low |
| 60 | Bare / sparse vegetation | Medium |
| 70 | Snow and ice | Low |
| 80 | Water bodies | Low (onshore) |
| 90 | Herbaceous wetland | Low |
| 95 | Mangroves | Low |

**Tips:**

- At least one criterion must be enabled to proceed.
- For offshore assessments, consider disabling Terrain Slope, Elevation, and LULC Suitability, as they are not meaningful over water.
- The Entropy and PCA methods are data-driven and therefore not available until the analysis in Step 4 completes. The wizard applies manual weights for the MCDA computation and reports the computed weights in the analysis log.


### Step 4: Analysis

Run the full wind resource assessment. The analysis executes on a background thread with progress reporting.

**Input summary:**

Before running, the step displays a summary of all configured inputs: domain coordinates, selected turbine, hub height, analysis year, grid resolution, installation type, data source, MCDA method, and enabled criteria.

**Analysis pipeline:**

1. **Data fetch** -- Downloads hourly wind speed and direction data from the selected data source for the analysis year. Also downloads terrain elevation (DEM) and land use/land cover data.
2. **Wind extrapolation** -- Extrapolates wind speed to the configured hub height using surface roughness and the wind profile power law.
3. **Weibull fitting** -- Fits the two-parameter Weibull distribution (shape \(k\), scale \(\lambda\)) to the hourly wind speed data at each grid cell. See [Equation WND-1](#weibull-probability-density-function).
4. **Power curve application** -- Applies the selected turbine's power curve to compute hourly capacity factors at each grid cell. See [Equation WND-4](#power-curve-model).
5. **Energy yield** -- Computes annual energy production and capacity factor at each cell.
6. **MCDA scoring** -- Evaluates each feasible cell against the configured criteria and weighting method.

**Run Analysis**: Starts the computation. The progress bar and log panel show real-time status. **Cancel** stops the analysis mid-stream.

**Analysis output** (passed to subsequent steps):

| Field | Description |
|-------|-------------|
| Total grid cells | Number of cells in the analysis domain |
| Feasible cells | Cells meeting the minimum capacity factor threshold |
| CF range | Minimum to maximum capacity factor across the domain |
| MCDA score range | Minimum to maximum suitability score |
| Computed weights | Per-criterion weights (for Entropy/PCA methods) |
| Hourly data | Per-cell hourly wind speed and direction arrays (used in Phase B) |


### Step 5: Results & Development Zones

Review the analysis results and generate wind development zones.

**Summary statistics:**

| Metric | Description |
|--------|-------------|
| Total Cells | Number of grid cells analyzed |
| Feasible Cells | Cells with CF above the minimum threshold |
| CF Min / Avg / Max | Capacity factor statistics across feasible cells |
| MCDA Score Range | Suitability score range |
| Installable Capacity | Estimated total capacity based on feasible area and turbine spacing |

**Map layers:**

The results are displayed as overlays on the map:

- **Wind resource map** -- Mean wind speed at hub height (color gradient from blue/low to red/high).
- **Capacity factor map** -- Annual CF at each grid cell.
- **Suitability map** -- MCDA composite score.
- **Development zones** -- Clustered feasible areas with buffer, suitable for wind farm siting.

**Actions:**

| Action | Description |
|--------|-------------|
| Show Results on Map | Toggle the spatial result overlays on the map |
| Generate Zones | Create development zone polygons from feasible cell clusters |
| Export GeoJSON | Save development zones as GeoJSON for use in GIS software |
| Add to Model | Import selected development zones as generator locations in the active system model |

**Tips:**

- Development zones group adjacent feasible cells into coherent areas. The zone buffer parameter from Step 2 controls how far apart cells can be and still belong to the same zone.
- After adding zones to the model, proceed to Phase B to generate availability profiles for the new wind generators.


---


## Phase B -- Advanced Analysis


### Step 6: Wind Characterization

Detailed statistical characterization of the wind regime using the hourly data fetched in Phase A. This step uses functions from `windrex`: `fit_weibull`, `weibull_pdf`, `weibull_mean_power_density`, `compute_wind_rose`, `compute_diurnal_pattern`, and `compute_seasonal_pattern`.

**Cell selector:**

Select an individual grid cell from the dropdown to inspect a specific location, or select **All Cells** to view aggregated statistics across the entire domain.

**Computed statistics:**

| Metric | Description |
|--------|-------------|
| Weibull Parameters | Shape factor \(k\) and scale factor \(\lambda\) (m/s). \(k\) controls the distribution width: \(k \approx 2\) is typical; \(k > 3\) indicates very consistent winds. \(\lambda\) is approximately 13% larger than the mean wind speed. See [Equation WND-1](#weibull-probability-density-function) |
| Mean Wind Speed | Arithmetic mean of all hourly wind speed measurements (m/s) |
| Wind Power Density | Mean kinetic energy flux per unit area (W/m^2), computed from the Weibull parameters. Values above 400 W/m^2 indicate excellent wind resource. See [Equation WND-3](#wind-power-density) |
| Dominant Direction | Most frequent wind direction sector based on the 16-sector wind rose |

**Charts (2x2 grid):**

1. **Weibull Distribution** -- Histogram of measured wind speeds overlaid with the fitted Weibull probability density function \(f(v)\). The fitted parameters \((k, \lambda)\) are shown in the legend. A good visual fit confirms that the Weibull model accurately represents the wind regime.

2. **Wind Rose** -- Polar bar chart showing wind frequency by direction sector (16 sectors, each 22.5 degrees). Bar colors indicate mean speed per sector. The wind rose identifies prevailing wind directions that are critical for turbine layout optimization in Step 8.

3. **Diurnal Pattern** -- Mean wind speed by hour of day (0-23). Some sites exhibit predictable diurnal cycles (e.g., coastal sites with stronger afternoon sea breezes). Sites with flat diurnal profiles have more consistent output.

4. **Seasonal Pattern** -- Mean wind speed by month (January-December). Most mid-latitude sites are windier in winter, while tropical sites may show monsoon-driven seasonality.

**Actions:**

| Action | Description |
|--------|-------------|
| Export Charts | Save the 2x2 chart grid as a PNG image (150 DPI) |

**Tips:**

- A Weibull shape factor \(k > 2\) indicates a relatively narrow speed distribution and more predictable energy output.
- Wind power density above 200 W/m^2 (IEC Class III threshold) is generally the minimum for commercial viability.
- When "All Cells" is selected, wind speeds from all cells are concatenated for aggregate statistics. Individual cell selection is useful for comparing microclimates within the domain.


### Step 7: Financial Analysis

Project-level financial evaluation for a representative wind farm, using the capacity and capacity factor computed in Phase A.

**Presets:**

| Preset | CAPEX ($/kW) | OPEX ($/kW/yr) | Lifetime |
|--------|-------------|----------------|----------|
| Onshore | 1,300 | 25 | 25 years |
| Offshore | 3,500 | 80 | 25 years |
| Custom | User-defined | User-defined | User-defined |

Selecting Onshore or Offshore automatically populates the cost fields with representative values from IRENA global benchmarks [**[48]**](../reference/bibliography.md#ref48). Select Custom to enter project-specific costs.

**Input parameters:**

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| CAPEX | 1,300 $/kW | 100-10,000 | Total installed capital cost per kW of nameplate capacity, including turbine, tower, foundation, balance of plant, and grid connection |
| OPEX | 25 $/kW/yr | 0-500 | Annual fixed operation and maintenance cost, including scheduled maintenance, insurance, and land lease |
| Discount Rate | 0.08 | 0.01-0.30 | Weighted average cost of capital (WACC) for discounted cash flow analysis |
| Lifetime | 25 years | 5-40 | Project economic lifetime |
| Electricity Price | 50 $/MWh | 5-500 | Expected average electricity selling price or avoided cost |
| Degradation Rate | 0.005/yr | 0-0.05 | Annual capacity degradation rate, accounting for blade erosion, drivetrain wear, and control system aging |

**Computed outputs:**

| Metric | Description |
|--------|-------------|
| LCOE | Levelized Cost of Energy ($/MWh). See [Equation WND-9](#lcoe) |
| NPV | Net Present Value ($). Sum of discounted revenues minus discounted costs over the project lifetime |
| IRR | Internal Rate of Return (%). The discount rate at which NPV equals zero |
| Payback Period | Simple payback (years). Time for cumulative undiscounted revenues to recover the initial investment |
| Annual Generation | Expected first-year energy production (MWh/yr) |
| Total CAPEX | Capacity times CAPEX rate ($) |

**Calculate**: Computes all financial metrics using `windrex.compute_wind_financials`.

**Sensitivity analysis:**

Select a parameter from the dropdown to visualize how LCOE changes when that parameter varies from 50% to 150% of its current value. Available sweep parameters:

- CAPEX ($/kW)
- Discount Rate
- Electricity Price ($/MWh)
- Capacity Factor

The sensitivity sweep runs on a background thread and renders a chart with the current operating point marked by a vertical dashed line.

**Actions:**

| Action | Description |
|--------|-------------|
| Calculate | Compute LCOE, NPV, IRR, and payback period |
| Export CSV | Save all input parameters and computed results to a CSV file |

**Tips:**

- The capacity factor used here is the gross CF from Phase A (before wake losses). For a more accurate LCOE that accounts for wake effects, run Step 8 first and adjust the CF accordingly.
- LCOE is most sensitive to capacity factor and CAPEX. Use the sensitivity chart to identify which parameter deserves the most attention in project development.
- For offshore projects, CAPEX values of 3,000-5,000 $/kW are typical, reflecting the additional cost of marine foundations, subsea cables, and offshore installation vessels.


### Step 8: Wake & Layout Analysis

Evaluates inter-turbine wake losses using the Jensen wake model and the wind rose computed in Step 6. Wake losses reduce the effective capacity factor of downstream turbines and are a primary consideration in wind farm layout design.

The Jensen (linear) wake model computes the velocity deficit behind each turbine as a function of downstream distance, thrust coefficient, and wake decay constant. See [Equation WND-5](#jensen-wake-velocity-deficit). The model uses the computed wind rose to weight directional contributions, so the result accounts for the actual prevailing wind directions at the site.

**Input parameters:**

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| Number of Turbines | 25 | 1-500 | Total turbines in the wind farm array. The model assumes a regular grid layout |
| Spacing | 7.0 D | 3.0-15.0 D | Distance between turbines in rotor diameters (D). Industry standard is 5-9D for onshore, 7-12D for offshore |
| Thrust Coefficient \(C_T\) | 0.80 | 0.10-1.00 | Turbine thrust coefficient at rated speed. Higher \(C_T\) means more energy extraction and larger wake deficit |

**Computed outputs:**

| Metric | Description |
|--------|-------------|
| Gross CF | Capacity factor before wake losses (from Phase A) |
| Array Efficiency | Fraction of gross energy that survives wake interactions (%). See [Equation WND-7](#array-efficiency) |
| Wake Loss | Energy lost to wake effects (1 - array efficiency, expressed as %) |
| Net CF | Effective capacity factor after wake losses (Gross CF times Array Efficiency) |
| Annual Generation | Expected net energy yield for the entire array (MWh/yr) |
| Array Summary | Number of turbines times rated power = total installed capacity |

**Calculate**: Launches the wake computation on a background thread using `windrex.compute_array_efficiency` and `windrex.compute_spacing_curve`.

**Spacing curve:**

A chart showing how array efficiency varies with turbine spacing from 3D to 15D. The currently selected spacing is marked on the curve with a vertical line and square marker. Closer spacing increases wake losses rapidly: moving from 7D to 5D typically increases losses by 5-10 percentage points, while going from 7D to 10D recovers only 2-3 points. This chart helps identify the optimal trade-off between land use and energy yield.

**Actions:**

| Action | Description |
|--------|-------------|
| Calculate | Run wake analysis on a background thread |
| Export CSV | Save array parameters, efficiencies, and energy yield to a CSV file |

**Tips:**

- Spacing below 5D is uncommon in practice due to excessive wake losses (>15%) and structural loading from turbulent wakes on downstream rotors.
- If no wind rose is available from Step 6 (e.g., if you advanced directly to Phase B), the model assumes a uniform 16-sector wind rose with 8 m/s mean speed per sector.
- The wake model uses a regular grid layout assumption. Real wind farm layouts optimized for the specific wind rose and terrain can achieve 2-5% higher array efficiency than a regular grid.


### Step 9: Availability Profile Generation

Generates hourly capacity factor profiles for wind generators in the current system model. These profiles are the time-series files consumed by the ESFEX optimization engine (see [Availability Profiles](../user-guide/availability-profiles.md) for format details).

The wizard automatically identifies all renewable generators with wind fuel types (Wind, Eolic, Turbine, Aerogenerador) in the active system, and computes a location-specific hourly profile for each one.

**Profile computation pipeline:**

For each selected generator instance, the wizard:

1. **Locates the nearest grid cell** -- Matches the generator's geographic position to the nearest cell from the Phase A analysis domain (up to 0.5 degrees distance).
2. **Applies the turbine power curve** -- If hourly wind speed data is available from Phase A, the wizard converts wind speeds to capacity factors using the turbine's power curve (either the custom curve from Step 2 or a default generic curve). This avoids a redundant API call.
3. **Falls back to fresh data fetch** -- If no nearby Phase A data exists (generator is outside the analysis domain), the wizard downloads hourly wind speed data for the generator's location and year via `windrex.compute_wind_hourly_cf`, then computes capacity factors.
4. **Writes CSV file** -- Saves an 8,760-row CSV file (one column per node) in the selected output directory. The file is automatically assigned as the generator's `Availability` profile in the model.

**Generator table columns:**

| Column | Description |
|--------|-------------|
| (checkbox) | Select/deselect individual generators for profile generation |
| Name | Generator display name |
| Unit Key | Internal generator identifier used for the output filename |
| Node | Node index in the system |
| Position | Geographic coordinates (latitude, longitude) |
| Status | Current status: Pending, Done (CF=x.xxx), Skipped, or Error |

**Configuration:**

| Setting | Description |
|---------|-------------|
| Output Directory | Directory where availability CSV files are saved. Defaults to `<config_dir>/availability/` |
| Select All / Deselect All | Toggle all generators at once |
| Browse | Choose a custom output directory |

After generation completes, the wizard displays a time-series preview chart of the first completed profile (8,760 hours, capacity factor 0-1) and updates each generator's `Availability` field in the model. A summary reports the number of profiles generated and the average capacity factor.

**Tips:**

- Generators without a geographic position (0, 0) are automatically skipped.
- Profiles generated from Phase A data are faster than fresh API calls, since the wind speed data is already in memory.
- Wind profiles typically show higher variability than solar profiles (no fixed diurnal pattern), so reviewing the preview chart is recommended to check for data quality issues.
- After generating profiles, you can proceed directly to running a simulation -- the model will use the newly created availability files.


---


## Mathematical Formulations


### Weibull Probability Density Function

The two-parameter Weibull distribution is the standard model for wind speed frequency distributions:

\[
f(v) = \frac{k}{\lambda} \left(\frac{v}{\lambda}\right)^{k-1} \exp\!\left[-\left(\frac{v}{\lambda}\right)^k\right] \tag{WND-1}
\]

| Symbol | Description |
|--------|-------------|
| \(v\) | Wind speed (m/s) |
| \(k\) | Shape parameter (dimensionless). Controls the width of the distribution. \(k \approx 2\) (Rayleigh) is typical for most sites |
| \(\lambda\) | Scale parameter (m/s). Related to the mean wind speed. Approximately 13% larger than the arithmetic mean |


### Weibull Mean Wind Speed

\[
\bar{v} = \lambda \, \Gamma\!\left(1 + \frac{1}{k}\right) \tag{WND-2}
\]

| Symbol | Description |
|--------|-------------|
| \(\bar{v}\) | Mean wind speed (m/s) |
| \(\Gamma(\cdot)\) | Gamma function |

This relates the Weibull parameters to the observed mean wind speed. When \(k = 2\) (Rayleigh distribution), \(\bar{v} \approx 0.886 \, \lambda\).


### Wind Power Density

\[
WPD = \frac{1}{2} \rho \, \overline{v^3} = \frac{1}{2} \rho \, \lambda^3 \, \Gamma\!\left(1 + \frac{3}{k}\right) \tag{WND-3}
\]

| Symbol | Description | Units |
|--------|-------------|-------|
| \(WPD\) | Wind power density | W/m^2 |
| \(\rho\) | Air density (standard: 1.225 kg/m^3 at sea level, 15 C) | kg/m^3 |
| \(\overline{v^3}\) | Mean of the cube of wind speed | m^3/s^3 |

Wind power density is the kinetic energy flux per unit area swept by the rotor. It is the primary metric for wind resource classification:

| IEC Class | WPD at 50 m (W/m^2) | Annual Mean Speed (m/s) |
|-----------|---------------------|------------------------|
| I (Excellent) | > 400 | > 7.5 |
| II (Good) | 300-400 | 6.5-7.5 |
| III (Moderate) | 200-300 | 5.5-6.5 |
| IV (Poor) | < 200 | < 5.5 |


### Power Curve Model

\[
P(v) = \begin{cases}
0 & v < v_{\text{cut-in}} \\[4pt]
P_{\text{rated}} \displaystyle\frac{v^3 - v_{\text{cut-in}}^3}{v_{\text{rated}}^3 - v_{\text{cut-in}}^3} & v_{\text{cut-in}} \leq v < v_{\text{rated}} \\[8pt]
P_{\text{rated}} & v_{\text{rated}} \leq v \leq v_{\text{cut-out}} \\[4pt]
0 & v > v_{\text{cut-out}}
\end{cases} \tag{WND-4}
\]

| Symbol | Description | Typical Range |
|--------|-------------|---------------|
| \(v_{\text{cut-in}}\) | Minimum wind speed for power generation | 2.5-4.0 m/s |
| \(v_{\text{rated}}\) | Wind speed at which rated power is reached | 11-15 m/s |
| \(v_{\text{cut-out}}\) | Maximum wind speed (turbine shuts down) | 20-30 m/s |
| \(P_{\text{rated}}\) | Nameplate capacity | MW |

The cubic interpolation in the mid-range approximates the relationship between wind speed and power extraction. When a manufacturer-provided power curve is available from the turbine database (Step 2), it replaces this generic model with point-by-point interpolation.


### Jensen Wake Velocity Deficit

\[
\frac{\Delta V}{V_0} = \frac{1 - \sqrt{1 - C_T}}{\left(1 + \dfrac{2 \, k_w \, x}{D}\right)^2} \tag{WND-5}
\]

| Symbol | Description | Units |
|--------|-------------|-------|
| \(\Delta V\) | Velocity deficit in the wake | m/s |
| \(V_0\) | Free-stream wind speed | m/s |
| \(C_T\) | Thrust coefficient of the upstream turbine | -- |
| \(k_w\) | Wake decay constant (0.04 onshore, 0.06 offshore) | -- |
| \(x\) | Downstream distance from the turbine | m |
| \(D\) | Rotor diameter | m |

The Jensen model assumes a linearly expanding wake cone behind each turbine. The velocity deficit decreases with distance as the wake entrains ambient air. The wake decay constant \(k_w\) is lower onshore (more turbulent boundary layer, faster wake recovery) than offshore.


### Superposition of Wakes

When a downstream turbine is in the wake of multiple upstream turbines, the combined velocity deficit is computed using the root-sum-of-squares (RSS) method:

\[
\Delta V_{\text{total}} = \sqrt{\sum_{i=1}^{N} \left(\Delta V_i\right)^2} \tag{WND-6}
\]

| Symbol | Description |
|--------|-------------|
| \(\Delta V_i\) | Velocity deficit from the \(i\)-th upstream turbine |
| \(N\) | Number of upstream turbines whose wake affects the downstream position |

The RSS method is the default superposition in the Jensen model. It produces more moderate combined deficits than linear addition and is consistent with experimental observations.


### Array Efficiency

\[
\eta_{\text{array}} = \frac{P_{\text{actual}}}{P_{\text{no wake}}} = \frac{\sum_{j=1}^{N_T} P\!\left(V_0 - \Delta V_{\text{total},j}\right)}{\sum_{j=1}^{N_T} P(V_0)} \tag{WND-7}
\]

| Symbol | Description |
|--------|-------------|
| \(\eta_{\text{array}}\) | Array efficiency (0-1). Fraction of gross energy surviving wake interactions |
| \(P_{\text{actual}}\) | Total power output with wake effects |
| \(P_{\text{no wake}}\) | Total power output if all turbines operated in free-stream conditions |
| \(N_T\) | Number of turbines in the array |

Array efficiency is computed for each wind direction sector using the wind rose frequencies, then averaged. Typical values range from 85-95% for well-spaced arrays.


### Air Density Correction

\[
\rho(z) = \rho_0 \left(\frac{T_0}{T_0 + L \, z}\right)^{\!gM / (R \, L)} \tag{WND-8}
\]

| Symbol | Description | Value |
|--------|-------------|-------|
| \(\rho_0\) | Sea-level air density | 1.225 kg/m^3 |
| \(T_0\) | Sea-level standard temperature | 288.15 K |
| \(L\) | Temperature lapse rate | 0.0065 K/m |
| \(z\) | Elevation above sea level | m |
| \(g\) | Gravitational acceleration | 9.80665 m/s^2 |
| \(M\) | Molar mass of dry air | 0.0289644 kg/mol |
| \(R\) | Universal gas constant | 8.31447 J/(mol K) |

Air density decreases with altitude, reducing the kinetic energy available in the wind. At 1,000 m elevation, air density is approximately 12% lower than at sea level, resulting in a proportional reduction in energy yield.


### LCOE

\[
LCOE = \frac{CRF(r, n) \times CAPEX + O\&M_{\text{annual}}}{E_{\text{annual}} \times \eta_{\text{array}}} \tag{WND-9}
\]

where the Capital Recovery Factor is:

\[
CRF(r, n) = \frac{r(1+r)^n}{(1+r)^n - 1}
\]

| Symbol | Description | Units |
|--------|-------------|-------|
| \(CAPEX\) | Total capital expenditure | $ |
| \(O\&M_{\text{annual}}\) | Annual operation and maintenance cost | $/yr |
| \(E_{\text{annual}}\) | Annual energy production (first year, before degradation) | MWh/yr |
| \(\eta_{\text{array}}\) | Array efficiency (from wake model; 1.0 if wake analysis not performed) | -- |
| \(r\) | Discount rate (WACC) | -- |
| \(n\) | Project lifetime | years |

The LCOE represents the minimum constant electricity price at which the project breaks even over its lifetime. Values are reported in $/MWh and can be compared against wholesale electricity prices or PPA rates to assess project viability [**[48]**](../reference/bibliography.md#ref48).


### Capacity Factor

\[
CF = \frac{E_{\text{annual}}}{P_{\text{rated}} \times 8760} \tag{WND-10}
\]

| Symbol | Description | Units |
|--------|-------------|-------|
| \(CF\) | Capacity factor (0-1) | -- |
| \(E_{\text{annual}}\) | Annual energy production | MWh |
| \(P_{\text{rated}}\) | Nameplate (rated) capacity | MW |
| \(8760\) | Hours per year | h |

The capacity factor is the ratio of actual energy output to the theoretical maximum. Typical values for modern onshore wind turbines range from 0.25 to 0.45, depending on the wind resource and turbine technology. Offshore sites routinely achieve 0.40-0.55.


---


## Scripting

All wizard computations are available as Python functions through the `windrex` library for batch processing and Jupyter notebooks:

```python
from windrex import (
    WindConfig,
    MCDAConfig,
    CriterionConfig,
    WindFinancialInputs,
    compute_wind_assessment,
    compute_wind_financials,
    compute_wind_hourly_cf,
    compute_array_efficiency,
    compute_spacing_curve,
    fit_weibull,
    weibull_pdf,
    weibull_mean_power_density,
    compute_wind_rose,
    load_turbine_database,
)

# --- Phase A: Resource assessment ---

config = WindConfig(
    turbine="Vestas_V112_3MW",
    hub_height=100,
    year=2022,
    grid_resolution=0.25,
    min_capacity_factor=0.25,
    installation="onshore",
    data_source="open_meteo",
)

mcda = MCDAConfig(
    method="manual",
    criteria={
        "capacity_factor": CriterionConfig(enabled=True, weight=0.40, direction="maximize"),
        "slope": CriterionConfig(enabled=True, weight=0.15, direction="minimize"),
        "elevation": CriterionConfig(enabled=True, weight=0.10, direction="minimize"),
        "lulc_score": CriterionConfig(enabled=True, weight=0.20, direction="maximize"),
        "dist_grid_km": CriterionConfig(enabled=True, weight=0.15, direction="minimize"),
    },
)

bounds = (21.5, -83.0, 22.5, -82.0)  # (south, west, north, east)
summary = compute_wind_assessment(bounds, config, mcda)

print(f"Feasible sites: {summary.feasible_cells}/{summary.total_cells}")
print(f"CF range: {summary.cf_min:.3f} - {summary.cf_max:.3f}")
print(f"Average CF: {summary.cf_avg:.3f}")

# --- Wind characterization ---

import numpy as np

# Use first cell's hourly data
cell_key = list(summary.hourly_data.keys())[0]
ws = np.asarray(summary.hourly_data[cell_key].wind_speed)
wd = np.asarray(summary.hourly_data[cell_key].wind_direction)

k, lam = fit_weibull(ws)
wpd = weibull_mean_power_density(k, lam)
wind_rose = compute_wind_rose(ws, wd)

print(f"Weibull: k={k:.2f}, lambda={lam:.2f} m/s")
print(f"Wind power density: {wpd:.0f} W/m2")

# --- Financial analysis ---

inputs = WindFinancialInputs(
    capacity_mw=3.0,
    capacity_factor=summary.cf_avg,
    capex_per_kw=1300,
    opex_per_kw_yr=25,
    discount_rate=0.08,
    lifetime_years=25,
    electricity_price=50.0,
    degradation_rate=0.005,
)

results = compute_wind_financials(inputs)
print(f"LCOE: ${results.lcoe:.2f}/MWh")
print(f"NPV: ${results.npv:,.0f}")
print(f"IRR: {results.irr:.1%}")

# --- Wake analysis ---

rotor_d = 112.0  # meters
efficiency = compute_array_efficiency(
    n_turbines=25,
    spacing_d=7.0,
    rotor_diameter=rotor_d,
    thrust_ct=0.80,
    wind_rose=wind_rose,
)
print(f"Array efficiency: {efficiency:.1%}")
print(f"Net CF: {summary.cf_avg * efficiency:.3f}")

spacings, efficiencies = compute_spacing_curve(
    rotor_diameter=rotor_d,
    thrust_ct=0.80,
    wind_rose=wind_rose,
    n_turbines=25,
)

# --- Availability profile ---

hourly_cf = compute_wind_hourly_cf(
    lat=22.0, lon=-82.5, year=2022,
    data_source="open_meteo",
    hub_height=100,
    turbine_key="Vestas_V112_3MW",
)
np.savetxt("wind_availability.csv", hourly_cf, delimiter=",", fmt="%.6f")
print(f"Profile mean CF: {np.mean(hourly_cf):.3f}")
```
