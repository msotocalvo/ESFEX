# Solar PV Assessment

Ground-mounted solar photovoltaic resource assessment using Multi-Criteria Decision Analysis (MCDA). Access via **Workflows > Solar PV Assessment**.

The wizard evaluates the technical and economic potential for solar PV deployment across a user-defined geographic domain. Starting from satellite-derived climate data and geospatial layers, it identifies feasible sites, ranks them through weighted multi-criteria scoring, computes capacity factors and LCOE, and generates development zones for integration into the system model. The resource assessment methodology follows established practices in renewable energy siting [**[55]**](../reference/bibliography.md#ref55), and the LCOE computation follows IRENA conventions [**[48]**](../reference/bibliography.md#ref48).

The wizard is organized in two phases:

- **Phase A (Steps 1-5)**: Resource Assessment. Define the study domain, configure the PV module and MCDA criteria, run the spatial analysis, and inspect results with development zone generation.
- **Phase B (Steps 6-9)**: Advanced Analysis. Characterize the solar resource in detail, evaluate project-level financials, analyze array layout and shading effects, and generate hourly availability profiles for the optimization engine.

All spatial and PV computations use the `solarex` library, which can also be used independently for scripting and batch analysis -- see [Scripting](#scripting).


---


## Phase A -- Resource Assessment


### Step 1: Domain Definition

Define the geographic bounding box for the solar PV assessment.

**Draw on map:**

Click the "Draw Rectangle" button, then click and drag on the map to define a bounding box. The wizard minimizes while drawing is active and restores automatically when the rectangle is completed. The domain area (in km^2) updates in real time using a spherical approximation based on the midpoint latitude.

**Manual entry:**

Enter bounding box coordinates directly: South latitude, West longitude, North latitude, East longitude. Click "Apply Coordinates" to set the domain, then "Show on Map" to visualize it.

**Validation:**

- North latitude must be greater than South latitude.
- East longitude must be greater than West longitude.
- A domain must be defined before proceeding to Step 2.

**Tips:**

- Start with a smaller domain for initial exploration, then expand after validating the workflow. Larger domains require more data downloads and computation time.
- The domain does not need to match system boundaries -- assess a larger region and select the best sites later.


### Step 2: Module & Assessment Configuration

Configure the PV module, orientation, and analysis parameters. The step is divided into four groups.

**Module selection:**

The California Energy Commission (CEC) module database is loaded on a background thread when the step initializes. The database contains thousands of commercially available modules with their electrical characteristics.

Filter controls narrow the selection:

| Filter | Description |
|--------|-------------|
| Manufacturer | Dropdown of all manufacturers in the CEC database, or "All" |
| Technology | Mono-c-Si, Multi-c-Si, CdTe, CIGS, a-Si, Thin Film, or "All" |
| Min Power / Max Power | STC power range in watts (0-700 W) |
| Bifacial Only | Show only bifacial modules |

The module table displays up to 500 matching modules (for UI performance). Columns: Manufacturer, Model, Technology, STC (W), Efficiency (%), Area (m^2), Bifacial. Selecting a row populates the details panel with full electrical characteristics: rated power (STC and PTC), technology, bifacial status, area, dimensions, cell count, Voc, Vmp, Isc, Imp, power temperature coefficient (gamma_pmax), and NOCT.

A module must be selected before proceeding.

**Orientation & tracking:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| Orientation | Latitude-optimal | "Latitude-optimal" sets tilt equal to the absolute site latitude and south-facing azimuth (180 degrees in the northern hemisphere). "Custom" enables manual tilt and azimuth entry. |
| Tilt | 20 degrees | Panel tilt angle. Only editable when orientation is "Custom". |
| Azimuth | 180 degrees | Panel azimuth. Only editable when orientation is "Custom". |
| Tracking | None (fixed tilt) | None, Horizontal single-axis, Vertical single-axis, or Dual-axis. Tracking increases energy yield but adds mechanical cost and complexity. |

**Analysis parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| Analysis Year | 2022 | Reference year for weather data retrieval (1979-2023 for ERA5). |
| Grid Resolution | 0.25 degrees | Spatial resolution of the analysis grid. Finer resolution increases accuracy but also computation time and data volume. |
| Installation Type | Ground-mount | Ground-mount or Floating. Affects the capacity density assumption. |
| Data Source | Open-Meteo (ERA5) | Weather data source. Open-Meteo provides fast access to ERA5 reanalysis data. NASA POWER uses MERRA-2 reanalysis. ERA5 via atlite is the slowest option (requires a CDS API key) but gives the most control. |
| Parallel Workers | Auto | Number of parallel threads for computation. 0 means automatic (uses all available cores). |

**Zone criteria:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| Min Capacity Factor | 0.15 | Minimum CF threshold for a grid cell to be classified as feasible. |
| Zone Buffer | 5 km | Buffer distance for clustering feasible cells into development zones. |

**Tips:**

- For a quick first assessment, use the default module, latitude-optimal orientation, and Open-Meteo data source.
- If the module database fails to load, ensure `pvlib` is installed: `pip install pvlib`.
- Finer grid resolutions (e.g. 0.05 degrees) are appropriate for small domains; use coarser resolutions (0.25 degrees or more) for country-scale assessments.


### Step 3: MCDA Criteria

Configure the Multi-Criteria Decision Analysis weighting and scoring. Each criterion evaluates one aspect of site suitability, scored on a 0-1 scale (0 = unsuitable, 1 = excellent).

**Criteria table:**

| Criterion | Direction | Default Weight | Description |
|-----------|-----------|----------------|-------------|
| Solar Capacity Factor | Maximize | 0.40 | Annual capacity factor computed from hourly irradiance simulation. Higher CF = higher score. |
| Terrain Slope | Minimize | 0.20 | Terrain slope extracted from digital elevation model (DEM). Flat terrain is preferred for ground-mount installations. |
| Elevation | Minimize | 0.05 | Elevation above sea level. Lower elevations are preferred (easier access, lower construction cost). |
| LULC Suitability | Maximize | 0.20 | Land Use / Land Cover suitability score. Each LULC class has a configurable suitability value. |
| Distance to Grid | Minimize | 0.15 | Distance to the nearest transmission line or substation. Closer sites reduce interconnection cost. Uses the system's node and line locations. |

Each criterion can be individually enabled or disabled via a checkbox.

**Weighting methods:**

| Method | Description |
|--------|-------------|
| Manual | Assign weights directly using spin boxes. Weights are normalized to sum to 1.0 during computation. |
| Entropy | Weights computed automatically from data using Shannon entropy. Criteria with more spatial variation receive higher weights. |
| PCA | Weights derived from first principal component loadings. Criteria that explain the most variance receive higher weights. |

When Entropy or PCA is selected, the weight spin boxes are disabled (weights are computed from data during the analysis).

**LULC suitability scores:**

An optional "Customize LULC Scores" checkbox reveals a table of land cover classes with editable suitability values:

| Code | Land Cover Class | Default Score |
|------|-----------------|---------------|
| 10 | Tree cover | 0.1 |
| 20 | Shrubland | 0.5 |
| 30 | Grassland | 0.7 |
| 40 | Cropland | 0.6 |
| 50 | Built-up | 0.2 |
| 60 | Bare / sparse vegetation | 0.8 |
| 70 | Snow and ice | 0.0 |
| 80 | Water bodies | 0.0 |
| 90 | Herbaceous wetland | 0.1 |
| 95 | Mangroves | 0.0 |

Default scores are loaded from the `solarex.DEFAULT_LULC_SCORES` dictionary.

**Tips:**

- At least one criterion must be enabled.
- For a solar-resource-dominated assessment, increase the capacity factor weight to 0.60 or higher.
- The entropy method is useful when you have no prior knowledge about the relative importance of criteria -- it lets the spatial data determine the weights.


### Step 4: Analysis

Run the solar PV resource assessment pipeline. Before execution, an input summary displays the domain coordinates, module details, orientation, tracking, analysis year, grid resolution, data source, MCDA method, and enabled criteria.

**Execution pipeline:**

1. **Data fetch** -- Downloads weather data (GHI, DNI, DHI, ambient temperature, wind speed) from the selected data source for the analysis year. For ERA5 via atlite, a valid CDS API key (~/.cdsapirc) is required. Downloads digital elevation model (DEM) and land use / land cover (LULC) classification.
2. **PV simulation** -- Runs a PV performance model for each grid cell using the selected module parameters (efficiency, temperature coefficient, NOCT). Computes hourly power output, annual energy yield, and capacity factor.
3. **MCDA scoring** -- Evaluates each criterion for every grid cell, normalizes scores to [0, 1], and computes the weighted composite suitability score. When using entropy or PCA weighting, the weights are computed in this step.
4. **Zone identification** -- Filters cells by the minimum capacity factor threshold and applies spatial clustering to identify contiguous feasible areas.

**Progress indicator:**

A progress bar (0-100%) and scrollable log display real-time status messages. The analysis runs on a background thread. A "Cancel" button stops the analysis at any point.

**Output:**

The analysis produces a `SolarPVAnalysisSummary` containing: total grid cells, feasible cells, capacity factor range (min/avg/max), average GHI (kWh/m^2/yr), MCDA score range, total installable capacity (MW), computed weights (if entropy/PCA), per-cell GeoDataFrame with all criteria scores, and hourly irradiance data per cell.

**Tips:**

- Open-Meteo and NASA POWER are the fastest data sources (seconds to minutes). ERA5 via atlite may take hours for large domains.
- Downloaded data is cached for reuse. Re-running the analysis with the same domain and year is faster.
- The "Next" button is only enabled after the analysis completes successfully.


### Step 5: Results & Development Zones

Inspect the analysis results and generate development zones for integration into the system model.

**Summary statistics:**

| Metric | Description |
|--------|-------------|
| Total Cells | Number of grid cells in the analysis domain |
| Feasible Cells | Cells meeting the minimum capacity factor threshold |
| CF Min / Avg / Max | Capacity factor statistics across all cells |
| Average GHI | Mean annual global horizontal irradiance (kWh/m^2/yr) |
| MCDA Score Range | Minimum and maximum composite suitability scores |
| Total Installable Capacity | Estimated installable capacity across all feasible cells (MW) |

When entropy or PCA weighting was used, the computed weights are displayed with a visual bar chart.

**Map visualization:**

- **Show Results on Map**: Overlays the per-cell results as a color-coded GeoJSON layer on the map. Each cell shows its composite MCDA score and capacity factor.
- **Clear Results**: Removes all solar PV overlays (domain, results, development zones) from the map.

**Development zone generation:**

Click "Generate Development Zones" to cluster feasible cells into development zone polygons. The algorithm uses the minimum capacity factor threshold from Step 2, a minimum MCDA score at the 50th percentile of feasible cells, and the configured buffer distance and grid resolution. Each zone reports: zone ID, area (km^2), number of sites, average CF, average MCDA score, and total capacity (MW).

Generated zones are automatically added to the system model as `GuiDevelopmentZone` elements (technology = "Solar") and displayed on the map.

**Export options:**

| Action | Description |
|--------|-------------|
| Export CSV | Save per-cell results (coordinates, CF, GHI, MCDA score, all criteria) as a CSV file |
| Export GeoJSON | Save development zone polygons as a GeoJSON file |

**Tips:**

- If no development zones are generated, try lowering the minimum capacity factor threshold in Step 2.
- Development zones can be refined manually in the studio after generation.
- The CSV export contains all per-cell data and can be loaded into GIS tools for further analysis.


---


## Phase B -- Advanced Analysis


### Step 6: GHI Characterization

Detailed solar resource characterization using the hourly irradiance and temperature data from Phase A.

**Cell selector:**

A dropdown allows choosing a specific grid cell by coordinates, or "All Cells" to view aggregated statistics across the entire domain.

**Computed statistics:**

| Metric | Description |
|--------|-------------|
| Mean GHI | Average global horizontal irradiance during daylight hours (W/m^2) and annual total (kWh/m^2/yr) |
| Peak Sun Hours | Equivalent hours per day at 1000 W/m^2 (PSH = daily GHI / 1000) |
| Performance Ratio | System-level efficiency accounting for temperature de-rating, computed from the module's efficiency, gamma_pmax, and NOCT |
| Clearness Index | Ratio of surface GHI to extraterrestrial radiation (K_t). Values near 0.7 indicate clear skies; below 0.4 indicates persistent cloud cover |
| Mean Cell Temperature | Average PV cell temperature during daylight hours, computed from ambient temperature using the NOCT model |

**Charts (2x2 grid):**

1. **GHI Distribution** -- Histogram of daytime irradiance values (GHI > 10 W/m^2) with probability density, showing the frequency of different irradiance levels.
2. **Monthly Irradiance** -- Bar chart of monthly cumulative GHI (kWh/m^2), revealing the seasonal pattern.
3. **Diurnal Irradiance** -- Average GHI by hour of day (0-23) with filled area, showing the typical daily irradiance bell curve.
4. **Cell Temperature vs Ambient** -- Scatter plot of cell temperature against ambient temperature during daylight hours. A diagonal reference line (T_cell = T_amb) highlights the temperature elevation from absorbed solar energy.

**Export Charts**: Saves all four charts as a single PNG file (150 DPI).

**Tips:**

- A clearness index above 0.5 indicates favorable conditions for solar PV deployment.
- Cell temperatures exceeding ambient by 20-30 degrees C are normal. The temperature coefficient (gamma_pmax) determines the power reduction.
- Compare individual cells to the aggregated statistics to identify spatial variation in the solar resource.


### Step 7: Financial Analysis

Project-level financial evaluation for a representative solar PV installation, using the capacity and average capacity factor computed in Phase A.

**Presets:**

| Preset | CAPEX ($/kW) | OPEX ($/kW/yr) | Lifetime |
|--------|-------------|----------------|----------|
| Ground-mount | 1,000 | 15 | 25 years |
| Floating | 1,400 | 25 | 25 years |
| Custom | User-defined | User-defined | User-defined |

**Input parameters:**

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| CAPEX | 1,000 $/kW | 100-10,000 | Total installed capital cost per kW of nameplate capacity |
| OPEX | 15 $/kW/yr | 0-500 | Annual fixed operation and maintenance cost |
| Discount Rate | 0.08 | 0.01-0.30 | Weighted average cost of capital for discounted cash flow analysis |
| Lifetime | 25 years | 5-40 | Project economic lifetime |
| Electricity Price | 50 $/MWh | 5-500 | Expected average electricity selling price or avoided cost |
| Degradation Rate | 0.005/yr | 0-0.05 | Annual capacity degradation rate [**[48]**](../reference/bibliography.md#ref48) |

**Computed outputs:**

| Metric | Description |
|--------|-------------|
| LCOE | Levelized Cost of Energy ($/MWh). Total discounted costs divided by total discounted generation (see [SPV-7](#lcoe) and [SPV-8](#crf)). |
| NPV | Net Present Value ($). Sum of discounted revenues minus discounted costs over the project lifetime. |
| IRR | Internal Rate of Return (%). The discount rate at which NPV equals zero. |
| Payback Period | Simple payback (years). Time for cumulative revenues to recover the initial investment. |
| Annual Generation | Expected first-year energy production (MWh/yr). |
| Total CAPEX | Capacity times CAPEX rate ($). |

**Sensitivity analysis:**

Select a parameter from the dropdown (CAPEX, Discount Rate, Electricity Price, or Capacity Factor) to visualize how LCOE changes when that parameter varies from 50% to 150% of its current value. The sweep runs on a background thread with 30 evaluation points and renders a chart with the current operating point marked by a vertical dashed line.

**Actions:**

| Action | Description |
|--------|-------------|
| Calculate | Compute LCOE, NPV, IRR, and payback period |
| Export CSV | Save all input parameters and computed results to a CSV file |

**Tips:**

- Start with the Ground-mount preset for a baseline, then adjust parameters to match the local market.
- The discount rate is the most influential parameter on LCOE. A 2 percentage point change in discount rate can shift LCOE by 10-20%.
- Compare the computed LCOE against the local electricity price to assess project viability.


### Step 8: Array / Shading Analysis

Evaluates inter-row shading losses and optional bifacial gain for the PV array layout. The analysis receives the site latitude, panel tilt, and capacity factor from upstream steps.

**Input parameters:**

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| Ground Coverage Ratio (GCR) | 0.40 | 0.15-0.90 | Ratio of module width to row pitch (see [SPV-4](#gcr)). Higher GCR = more capacity per unit area but more inter-row shading. |
| Module Height | 2.0 m | 1.0-4.0 m | Vertical extent of the module row. Affects shadow length and bifacial rear-side irradiance. |
| Ground Albedo | 0.25 | 0.0-0.80 | Surface reflectivity. Higher albedo benefits bifacial modules (e.g., 0.50 for white gravel, 0.25 for grass). |
| Bifacial Module | Off | On/Off | Enable bifacial gain calculation (see [SPV-6](#bifacial-gain)). Only available if the module selected in Step 2 is bifacial. |

**Computed outputs:**

| Metric | Description |
|--------|-------------|
| Shading Loss | Fractional energy loss due to inter-row shading at the selected GCR (%) (see [SPV-5](#shading-loss)). |
| Bifacial Gain | Additional energy from rear-side irradiance, if bifacial is enabled (+%). |
| Net Efficiency | Combined effect: \((1 - \text{shading\_loss}) \times (1 + \text{bifacial\_gain})\). |
| Gross CF | Capacity factor before array-level losses (from Phase A). |
| Net CF | Capacity factor after applying net efficiency. |
| Annual Generation | Expected energy yield at net CF (MWh/yr). |

**GCR shading curve:**

A chart showing how shading loss varies with GCR from 0.15 to 0.90. The selected GCR is marked on the curve with a vertical dashed line and a square marker at the intersection. Lower GCR values reduce shading but require more land area per MW installed. The computation uses the geometric shading model that accounts for latitude, tilt, and module height.

**Actions:**

| Action | Description |
|--------|-------------|
| Calculate | Run shading loss and bifacial gain computation on a background thread |
| Export CSV | Save array analysis parameters and results (latitude, tilt, GCR, module height, albedo, shading loss, bifacial gain, net efficiency, gross CF, net CF, annual generation) |

**Tips:**

- GCR values of 0.30-0.45 are typical for fixed-tilt ground-mount systems.
- Tracking systems generally tolerate higher GCR because the effective row-to-row spacing changes throughout the day.
- Bifacial modules can recover 5-15% additional energy depending on albedo and module height.
- White gravel ground cover (albedo 0.50) combined with elevated modules (3-4 m height) maximizes bifacial gain.


### Step 9: Availability Profile Generation

Generates hourly capacity factor profiles for solar PV generators in the current system model. These profiles are the time-series files consumed by the ESFEX optimization engine (see [Availability Profiles](../user-guide/availability-profiles.md) for format details).

The wizard automatically identifies all renewable generators with solar fuel types (Sun, Solar, PV, Photovoltaic) in the active system and lists them in a table.

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

| Setting | Default | Description |
|---------|---------|-------------|
| Output Directory | `<config_dir>/availability/` | Directory where availability CSV files are saved. Configurable via the Browse button. |
| Select All / Deselect All | All selected | Toggle all generators at once |

**Profile computation:**

For each selected generator instance, the wizard:

1. **Locates the nearest grid cell** -- Matches the generator's geographic position to the nearest cell from the Phase A analysis domain (up to 0.5 degrees distance).
2. **Converts irradiance to capacity factors** -- If hourly GHI and temperature data are available from Phase A, applies the irradiance-to-CF model using the module parameters (efficiency, gamma_pmax, NOCT). This avoids a redundant API call.
3. **Falls back to fresh data fetch** -- If no nearby Phase A data exists (generator is outside the analysis domain), downloads hourly weather data for the generator's location and year from the configured data source, then computes capacity factors.
4. **Writes CSV file** -- Saves an 8,760-row CSV file (one column per node) in the selected output directory. The file is automatically assigned as the generator's availability profile in the model.

Generators with no geographic position (0, 0) are automatically skipped.

**Preview chart:**

After generation completes, a time-series plot of the first completed profile (8,760 hours, capacity factor 0-1) is displayed.

**Summary:**

Reports the number of profiles generated and the average capacity factor across all completed generators.

**Tips:**

- Profiles generated from Phase A data are faster than fresh API calls, since the weather data is already in memory.
- After generating profiles, you can proceed directly to running a simulation -- the model will use the newly created availability files.
- Each unit key produces one CSV file with columns for each node. Multiple generator instances sharing the same unit key are written into the same file.


---


## Mathematical Formulations


### MCDA Weighted Sum Score

The composite suitability score for grid cell \(i\) is computed as:

\[
S_i = \sum_{j=1}^{M} w_j \cdot c_{i,j} \tag{SPV-1}
\]

| Symbol | Description |
|--------|-------------|
| \(S_i\) | Composite suitability score for cell \(i\) (0-1) |
| \(w_j\) | Weight of criterion \(j\) (normalized so \(\sum w_j = 1\)) |
| \(c_{i,j}\) | Normalized score of criterion \(j\) at cell \(i\) (0-1) |
| \(M\) | Number of enabled criteria |

For "maximize" criteria, values are normalized as \(c = (x - x_{\min}) / (x_{\max} - x_{\min})\). For "minimize" criteria, the normalization is inverted: \(c = (x_{\max} - x) / (x_{\max} - x_{\min})\).


### GHI to POA Irradiance

The plane-of-array (POA) irradiance depends on the module tilt and azimuth relative to the sun position:

\[
G_{\text{POA}} = G_b \cdot \cos\theta_i + G_d \cdot \frac{1 + \cos\beta}{2} + G \cdot \rho \cdot \frac{1 - \cos\beta}{2} \tag{SPV-2}
\]

| Symbol | Description |
|--------|-------------|
| \(G_b\) | Beam (direct normal) component of irradiance (W/m^2) |
| \(G_d\) | Diffuse horizontal irradiance (W/m^2) |
| \(G\) | Global horizontal irradiance (W/m^2) |
| \(\theta_i\) | Angle of incidence between the sun vector and the panel normal |
| \(\beta\) | Panel tilt angle (degrees from horizontal) |
| \(\rho\) | Ground reflectance (albedo) |


### Capacity Factor

\[
CF = \frac{E_{\text{annual}}}{P_{\text{rated}} \times 8760} \tag{SPV-3}
\]

| Symbol | Description |
|--------|-------------|
| \(CF\) | Annual capacity factor (0-1) |
| \(E_{\text{annual}}\) | Total energy produced in one year (MWh) |
| \(P_{\text{rated}}\) | Nameplate rated power (MW) |
| \(8760\) | Hours in a non-leap year |


### Ground Coverage Ratio (GCR)

\[
GCR = \frac{W_{\text{module}}}{D_{\text{row}}} \tag{SPV-4}
\]

| Symbol | Description |
|--------|-------------|
| \(GCR\) | Ground coverage ratio (dimensionless, typically 0.25-0.50) |
| \(W_{\text{module}}\) | Width of the module row projected onto the ground (m) |
| \(D_{\text{row}}\) | Row-to-row pitch (center to center, m) |

Higher GCR values pack more capacity into a given land area but increase inter-row shading during low sun angles.


### Shading Loss Fraction

The inter-row shading loss depends on the geometry of the array and the sun path at the site latitude:

\[
f_{\text{shade}} = \max\left(0, \; 1 - \frac{D_{\text{row}} - W_{\text{module}} \cdot \cos\beta}{H \cdot \tan\alpha_s}\right) \cdot f_{\text{time}} \tag{SPV-5}
\]

| Symbol | Description |
|--------|-------------|
| \(f_{\text{shade}}\) | Annual energy-weighted shading loss fraction (0-1) |
| \(D_{\text{row}}\) | Row-to-row pitch (m) |
| \(W_{\text{module}}\) | Module row width (m) |
| \(\beta\) | Panel tilt angle (degrees) |
| \(H\) | Module height / vertical extent (m) |
| \(\alpha_s\) | Solar altitude angle (degrees) |
| \(f_{\text{time}}\) | Time-weighted fraction of hours when shading occurs |

The implementation integrates this geometry over the annual sun path for the site latitude using the `solarex.compute_gcr_shading_loss` function.


### Bifacial Gain

\[
G_{\text{bi}} = BG \times \rho_{\text{ground}} \times f_{\text{view}} \tag{SPV-6}
\]

| Symbol | Description |
|--------|-------------|
| \(G_{\text{bi}}\) | Bifacial energy gain factor (fractional, e.g. 0.10 = 10% gain) |
| \(BG\) | Module bifaciality factor (ratio of rear to front efficiency, typically 0.65-0.85) |
| \(\rho_{\text{ground}}\) | Ground albedo (0-1) |
| \(f_{\text{view}}\) | View factor from the module rear surface to the ground, depending on GCR and module height |

The net capacity factor after array-level adjustments is:

\[
CF_{\text{net}} = CF_{\text{gross}} \times (1 - f_{\text{shade}}) \times (1 + G_{\text{bi}})
\]


### Levelized Cost of Energy (LCOE) [**[48]**](../reference/bibliography.md#ref48)

\[
LCOE = \frac{CRF(r, n) \times CAPEX + O\&M_{\text{annual}}}{E_{\text{annual}}} \tag{SPV-7}
\]

| Symbol | Description |
|--------|-------------|
| \(LCOE\) | Levelized cost of energy ($/MWh) |
| \(CRF\) | Capital Recovery Factor (see SPV-8) |
| \(CAPEX\) | Total capital expenditure ($) |
| \(O\&M_{\text{annual}}\) | Annual operation and maintenance cost ($) |
| \(E_{\text{annual}}\) | Annual energy generation (MWh), accounting for degradation |
| \(r\) | Discount rate |
| \(n\) | Project lifetime (years) |

When degradation is included, the annual generation in year \(y\) is \(E_y = E_1 \times (1 - d)^{y-1}\), where \(d\) is the annual degradation rate and \(E_1\) is the first-year generation.


### Capital Recovery Factor (CRF)

\[
CRF(r, n) = \frac{r(1+r)^n}{(1+r)^n - 1} \tag{SPV-8}
\]

| Symbol | Description |
|--------|-------------|
| \(r\) | Discount rate (annual) |
| \(n\) | Project lifetime (years) |

Converts a lump-sum present value into an equivalent uniform annual cost over \(n\) years at discount rate \(r\). Used to annualize capital expenditures for LCOE computation.


### Temperature De-rating

\[
P_{\text{actual}} = P_{\text{STC}} \times \left(1 + \gamma \times (T_{\text{cell}} - 25)\right) \tag{SPV-9}
\]

| Symbol | Description |
|--------|-------------|
| \(P_{\text{actual}}\) | Actual power output (W) |
| \(P_{\text{STC}}\) | Rated power at Standard Test Conditions (W) |
| \(\gamma\) | Power temperature coefficient (%/degrees C, typically -0.3 to -0.5 %/degrees C) |
| \(T_{\text{cell}}\) | Cell temperature (degrees C) |
| \(25\) | Reference temperature at STC (degrees C) |

Cell temperature is estimated from ambient temperature using the NOCT model:

\[
T_{\text{cell}} = T_{\text{amb}} + \frac{NOCT - 20}{800} \times G
\]

where \(NOCT\) is the Nominal Operating Cell Temperature (typically 42-48 degrees C) and \(G\) is the irradiance on the module surface (W/m^2).


---


## Scripting

All wizard computations are available through the `solarex` library for batch processing and Jupyter notebooks:

```python
from solarex import (
    CriterionConfig,
    MCDAConfig,
    SolarFinancialInputs,
    compute_pv_financials,
    compute_pv_lcoe_sensitivity,
    compute_solar_hourly_cf,
    compute_gcr_shading_loss,
    compute_gcr_curve,
    compute_bifacial_gain,
    compute_clearness_index,
    compute_peak_sun_hours,
    compute_performance_ratio,
    load_module_database,
)
from solarex.config import SolarConfig

# 1. Load module database and select a module
modules = load_module_database()
module = next(m for m in modules if "Canadian" in m.manufacturer and m.stc_power_w > 400)
print(f"Selected: {module.manufacturer} {module.name}, {module.stc_power_w:.0f} W")

# 2. Compute hourly capacity factors for a location
cf_hourly = compute_solar_hourly_cf(
    lat=21.5, lon=-82.3, year=2022, data_source="open_meteo",
    efficiency=module.efficiency,
    gamma_pmax=module.gamma_pmax,
    t_noct=module.t_noct,
)
import numpy as np
print(f"Annual CF: {np.mean(cf_hourly):.3f}")

# 3. Financial analysis
inputs = SolarFinancialInputs(
    capacity_mw=50.0,
    capacity_factor=float(np.mean(cf_hourly)),
    capex_per_kw=1000,
    opex_per_kw_yr=15,
    discount_rate=0.08,
    lifetime_years=25,
    electricity_price=50.0,
    degradation_rate=0.005,
)
results = compute_pv_financials(inputs)
print(f"LCOE: ${results.lcoe:.2f}/MWh, NPV: ${results.npv:,.0f}, IRR: {results.irr:.1%}")

# 4. Array shading analysis
shading_loss = compute_gcr_shading_loss(
    latitude=21.5, tilt=21.5, gcr=0.40, module_height=2.0,
)
bifacial = compute_bifacial_gain(albedo=0.25, gcr=0.40, module_height=2.0, tilt=21.5)
net_cf = float(np.mean(cf_hourly)) * (1 - shading_loss) * (1 + bifacial)
print(f"Shading loss: {shading_loss:.1%}, Bifacial gain: {bifacial:.1%}, Net CF: {net_cf:.3f}")

# 5. LCOE sensitivity sweep
sweep_values = np.linspace(500, 1500, 20)
lcoe_values = compute_pv_lcoe_sensitivity(inputs, "capex_per_kw", sweep_values.tolist())
```

See the [solarex API Reference](../api/solarex.md) for full parameter documentation.
