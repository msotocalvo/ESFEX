# OTEC Assessment

Ocean Thermal Energy Conversion (OTEC) site assessment and plant design wizard. Access via **Workflows > OTEC Assessment**.

OTEC exploits the temperature difference between warm surface seawater and cold deep-ocean water to drive a thermodynamic cycle and generate electricity. The technology is viable in tropical and subtropical waters (approximately ±30° latitude) where the temperature differential between the surface and deep ocean exceeds 20 °C year-round. The wizard uses the [otex](https://pypi.org/project/otex/) library for plant sizing, economic modelling, and uncertainty analysis, combined with CMEMS (Copernicus Marine Environment Monitoring Service) oceanographic reanalysis data for site characterization.

The wizard is organized in two phases:

- **Phase A (Steps 1-4)**: Site Assessment. Define the ocean domain, configure plant parameters, run the resource analysis against CMEMS data, and identify development zones.
- **Phase B (Steps 5-11)**: Advanced Analysis. Characterize the thermal resource, decompose component-level economics, quantify uncertainty and sensitivity, model off-design operation, size the cold water pipe, and generate availability profiles for integration with the optimizer.

All Phase B computations use the `otex` library's analysis modules (Monte Carlo, Tornado, Sobol, off-design operation) and the pure-function models in `esfex.models.otec_models`, both of which can be used independently for scripting and batch analysis.


---


## Phase A -- Site Assessment


### Step 1: Domain Definition

Define the geographic bounding box for the OTEC analysis. OTEC is thermodynamically viable only in waters where the temperature difference between the warm surface layer and cold deep water exceeds approximately 20 °C, which restricts candidate regions to tropical and subtropical oceans (roughly ±30° latitude).

**Draw on map:**

Click "Draw Rectangle", then click and drag on the map to define the analysis domain. The wizard minimizes while the map interaction is active and restores automatically when the rectangle is complete.

**Manual coordinates:**

Enter the bounding box directly as south/north latitude and west/east longitude values. Latitude ranges from -90° to 90°; longitude ranges from -180° to 180°. North must be greater than south, and east must be greater than west.

After defining the domain, the approximate area in km² is displayed, computed using the mid-latitude correction:

\[
A \approx (\Delta \phi \times 111.32) \times (\Delta \lambda \times 111.32 \times \cos \bar{\phi})
\]

where \(\Delta \phi\) and \(\Delta \lambda\) are the latitude and longitude spans in degrees, and \(\bar{\phi}\) is the mid-latitude.

**Tips:**

- Start with a broad domain to survey the thermal resource, then narrow to promising sub-regions in subsequent runs.
- OTEC requires deep water (600-3000 m) close to shore. Volcanic island coastlines with steep bathymetric profiles are typically the best candidates.
- The domain should cover enough ocean area to capture spatial variation in temperature differential, but excessively large domains increase data download time and computation.


### Step 2: Plant Configuration

Configure the OTEC plant design parameters, evaluation grid, and development zone criteria.

**Main parameters:**

| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| Thermodynamic cycle | (selector) | Rankine Closed | The power conversion cycle. Options: Rankine Closed (standard closed-cycle with working fluid), Rankine Open (flash evaporation of seawater), Rankine Hybrid (combined features of open and closed), Kalina (ammonia-water mixture with variable boiling point), Uehara (advanced cycle with higher theoretical efficiency). |
| Working fluid | (selector) | Ammonia | Working fluid for the thermodynamic cycle. Options: Ammonia, R134a, R245fa, Propane, Isobutane. Ammonia is the standard choice for closed-cycle OTEC due to favorable heat transfer properties. |
| Gross power | 10-500 MW | 136 MW | Nameplate gross power output of the plant before parasitic losses (pumping, transmission). |
| Cost level | Low / High | Low Cost | Cost assumption scenario. Low cost reflects optimistic technology learning; high cost reflects current state of the art. |
| Installation | Offshore / Onshore | Offshore | Plant siting. Offshore plants are moored or floating platforms; onshore plants are built on the coastline with long cold water intake pipes. |
| Analysis year | 1994-2023 | 2020 | Calendar year for CMEMS ocean temperature data retrieval. Determines the specific daily temperature profiles used. |

**Depth limits:**

| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| Minimum depth | 100-5000 m | 600 m | Shallowest water depth considered. Sites shallower than this are excluded from the analysis. |
| Maximum depth | 100-5000 m | 3000 m | Deepest water depth considered. Sites deeper than this are excluded. |

Minimum depth must be strictly less than maximum depth.

**Evaluation grid:**

| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| Grid resolution | 0.05-2.0° | 0.25° | Spacing of evaluation points within the domain. Finer resolution produces more evaluation sites but increases computation time proportionally. At 0.25° spacing, a 10° x 10° domain produces approximately 1,600 evaluation points. |

**Development zone criteria:**

| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| LCOE threshold | 0.01-1.00 $/kWh | 0.15 $/kWh | Maximum LCOE for a site to be included in a development zone. Sites with LCOE below this threshold are considered economically feasible. |
| Zone buffer | 1-100 km | 10 km | Buffer radius applied around clusters of feasible sites when generating development zone polygons. Larger buffers merge nearby sites into contiguous zones. |

**Advanced parameters** (collapsed by default):

| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| Discount rate | 0.01-0.30 | 0.10 | Annual discount rate for LCOE calculation [**[48]**](../reference/bibliography.md#ref48). |
| Plant lifetime | 10-50 years | 30 years | Economic lifetime over which capital costs are annualized. |
| Availability | 0.5-1.0 | 0.914 | Plant availability factor accounting for scheduled and unscheduled downtime. The default 91.4% reflects typical OTEC operating experience including biofouling maintenance. |

**Tips:**

- The default 136 MW gross power corresponds to a large commercial-scale OTEC plant. For initial feasibility screening, smaller plants (10-50 MW) may be more realistic for island systems.
- The Rankine Closed cycle with Ammonia is the most mature and well-characterized configuration. Use other cycle/fluid combinations for comparative studies.
- When comparing offshore vs. onshore installation, note that onshore plants have lower structural costs but require longer cold water pipes, increasing pumping losses.


### Step 3: Analysis

Run the OTEC resource analysis in a background thread.

**Workflow:**

1. **CMEMS data retrieval** -- Downloads daily ocean temperature reanalysis data from the Copernicus Marine Service (dataset `cmems_mod_glo_phy_my_0.083deg_P1D-m`) at two depth levels: warm-water intake (~20 m) and cold-water intake (~1000 m).
2. **Site evaluation** -- For each grid point within the domain, the analyzer extracts median warm and cold water temperatures, computes the temperature differential, estimates distance to shore, and derives transmission efficiency.
3. **Plant sizing** -- Calls the OTEX on-design analysis engine (`otex.plant.off_design_analysis.on_design_analysis`) which sweeps temperature pairs internally and returns the plant configuration with the lowest LCOE for each site.
4. **Feasibility filtering** -- Sites are marked feasible if their LCOE falls below the configured threshold and the depth is within the specified limits.

A progress bar and log window display real-time status. The analysis can be cancelled at any time.

**Input summary:** Before running, the step displays the configured domain, cycle type, working fluid, gross power, cost level, depth range, and analysis year for verification.

**Output:** An `OTECSummary` object containing:

| Field | Description |
|-------|-------------|
| Total sites | Number of grid points evaluated |
| Feasible sites | Number of sites with LCOE below the threshold |
| LCOE min / avg / max | Range of levelized cost across feasible sites ($/kWh) |
| Total capacity | Aggregate installable capacity across all feasible sites (MW) |
| Average CF | Mean capacity factor across feasible sites |
| Daily data | Per-cell daily warm and cold water temperature time series |

**Tips:**

- The first run for a given domain and year requires downloading CMEMS data, which may take several minutes depending on domain size and network speed. Subsequent runs with the same domain use cached data.
- If no feasible sites are found, try increasing the LCOE threshold or expanding the domain to include waters with stronger temperature differentials.


### Step 4: Results & Development Zones

View the analysis results, visualize them on the map, and generate development zones.

**Summary statistics:**

A summary panel displays the key metrics from the analysis: total and feasible site counts, LCOE range (min/avg/max), total installable capacity, and average capacity factor.

**Map visualization:**

- **Show Results** -- Displays feasible sites as a GeoJSON overlay on the map, color-coded by LCOE.
- **Clear Results** -- Removes all OTEC overlays (domain rectangle, results, and development zones) from the map.

**Development zones:**

Click "Generate Zones" to cluster feasible sites into development zone polygons. The algorithm applies the configured LCOE threshold and buffer radius to group nearby feasible sites into contiguous geographic zones. Each zone is characterized by:

- Zone ID
- Area (km²)
- Number of constituent sites
- Average LCOE ($/kWh)
- Total installable capacity (MW)

When a system model is loaded in the studio, generated zones are automatically added as `GuiDevelopmentZone` elements with technology type "OTEC", linking the resource assessment to the energy system model.

**Export:**

| Format | Content |
|--------|---------|
| **CSV** | Per-site results table (latitude, longitude, depth, temperatures, LCOE, net power, capacity factor, feasibility flag). Geometry column excluded. |
| **GeoJSON** | Development zone polygons with attributes (zone_id, area_km2, num_sites, avg_lcoe, total_capacity_mw). |


---


## Phase B -- Advanced Analysis

Phase B steps provide deep-dive analyses that build on the Phase A results. Each step receives data from the analysis summary and passes forward to subsequent steps.


### Step 5: Thermal Characterization

Analyze the ocean temperature characteristics, temperature differential patterns, and thermodynamic efficiency potential of the selected domain.

**Cell selector:** Choose a specific grid cell or "All Cells" to aggregate data across the entire domain.

**Statistics panel:**

| Metric | Description |
|--------|-------------|
| Mean \(\Delta T\) | Average temperature difference between warm and cold water (°C) |
| Min \(\Delta T\) | Minimum observed \(\Delta T\) (°C) -- corresponds to the least favorable operating conditions |
| Max \(\Delta T\) | Maximum observed \(\Delta T\) (°C) -- corresponds to peak performance conditions |
| Mean warm water | Average surface water temperature (°C) |
| Mean cold water | Average deep water temperature (°C) |
| Carnot efficiency | Theoretical maximum efficiency (see [OTEC-1](#carnot-efficiency)) |
| CF range | Capacity factor range based on \(\Delta T\) variation relative to the design point |

**Charts (2x2 panel):**

1. **Temperature difference distribution** -- Histogram of daily \(\Delta T\) values. The spread indicates how much the resource varies over the year. A narrow distribution centered well above 20 °C is ideal.
2. **Monthly temperature profiles** -- Side-by-side bars showing monthly mean warm and cold water temperatures. Reveals seasonal heating/cooling cycles.
3. **Monthly \(\Delta T\) variation** -- Bar chart with error bars showing monthly mean and standard deviation of \(\Delta T\). Identifies months with weakest resource.
4. **Monthly capacity factor** -- Line plot of estimated monthly CF based on the ratio of actual \(\Delta T\) to design-point \(\Delta T\). Shows the seasonal production profile.

The capacity factor at each time step is computed as:

\[
CF(t) = CF_{\text{nom}} \times \min\!\left(\frac{\Delta T(t)}{\Delta T_{\text{design}}},\; 1.2\right) \tag{OTEC-1a}
\]

where \(CF_{\text{nom}}\) is the nominal availability (default 0.914) and \(\Delta T_{\text{design}}\) is the median temperature difference at the design point. The 1.2 upper bound allows slight over-performance on days warmer than design conditions.

Charts can be exported as PNG or PDF.


### Step 6: Component Economics

Component-level CAPEX breakdown and LCOE analysis using the OTEX economic model.

**Cost components:**

The OTEX library provides a detailed breakdown of capital expenditures by component. The following cost categories are reported:

| Component | Description |
|-----------|-------------|
| Turbine | Turbine-generator set |
| Evaporator | Warm water heat exchanger (evaporator) |
| Condenser | Cold water heat exchanger (condenser) |
| Pumps | Seawater circulation pumps (warm and cold water) |
| Pipes | Cold water pipe (CWP) and warm water intake pipe |
| Structure/Mooring | Mooring system and structural components |
| Platform | Floating platform or onshore facility |
| Deployment | Installation and deployment costs |
| Cable | Submarine power cable to shore |
| Management | Project management and engineering |

**Summary metrics:**

| Metric | Units | Description |
|--------|-------|-------------|
| Total CAPEX | $ | Sum of all component costs |
| Annual OPEX | $/yr | Annual operation and maintenance expenditure |
| LCOE | ct/kWh ($/MWh) | Levelized cost of energy from the OTEX economic model |

**CAPEX breakdown chart:** Horizontal bar chart showing each component's contribution to total capital cost, with dollar values annotated on each bar. This visualization identifies the dominant cost drivers -- typically the cold water pipe and heat exchangers for large OTEC plants.

**Export:** Component breakdown as CSV (component name, value, unit).

**Tips:**

- Compare low-cost and high-cost scenarios to understand the range of economic outcomes. The difference is primarily driven by heat exchanger and CWP cost assumptions.
- The LCOE reported here is the on-design value computed by OTEX at the median temperature conditions. Step 9 (Off-Design Operation) provides a more realistic estimate accounting for daily temperature variations.


### Step 7: Uncertainty Analysis (Monte Carlo)

Probabilistic assessment of OTEC plant economics through Monte Carlo simulation with Latin Hypercube Sampling (LHS) [**[12]**](../reference/bibliography.md#ref12).

LHS partitions each input variable's probability distribution into \(N\) equally probable strata and draws exactly one sample from each stratum, ensuring comprehensive coverage of the input space with fewer samples than simple random sampling.

**Configuration:**

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| Number of samples | 100-5000 | 1000 | Number of Monte Carlo iterations. Higher counts yield smoother distributions. |
| Random seed | 0-99999 | 42 | Seed for reproducibility. |

**Input conditions:** The analysis uses median warm water temperature (\(T_{WW}\)) and cold water temperature (\(T_{CW}\)) from the Phase A analysis as the central values. The OTEX `UncertaintyConfig` applies default uncertainty ranges to all plant parameters (cost factors ~±20%, temperature ~±5%, cycle efficiency ~±10%).

**Run Monte Carlo:** Executes in a background thread using `otex.analysis.MonteCarloAnalysis`. A progress indicator is shown during computation.

**Statistics panel:**

| Output | Metrics |
|--------|---------|
| LCOE | Mean, standard deviation, 5th percentile (P5), 95th percentile (P95), 90% confidence interval |
| Net power | Mean, standard deviation |
| CAPEX | Mean, standard deviation |

**Charts (2x2 panel):**

1. **LCOE histogram** -- Distribution of LCOE outcomes across all Monte Carlo samples.
2. **Net power histogram** -- Distribution of net power output.
3. **LCOE cumulative distribution (CDF)** -- Probability of achieving LCOE at or below each value. Read off the probability of meeting a target LCOE from this curve.
4. **Parameter correlations** -- Spearman rank correlation coefficients between input parameters and LCOE. Bars are colored red (positive correlation, i.e., increasing the parameter increases LCOE) or blue (negative correlation). The chart ranks the top 10 most correlated parameters.

**Export:** Statistics and correlations as CSV.

**Tips:**

- Start with 1,000 samples for rapid exploration, then increase to 3,000-5,000 for final analysis when smooth distributions are needed.
- The correlation chart identifies which input uncertainties contribute most to LCOE uncertainty. Focus data collection and risk mitigation efforts on the highest-correlated parameters.
- The P5-P95 range gives the central 90% of outcomes. If this range is wide relative to the mean LCOE, the project carries significant economic risk.


### Step 8: Sensitivity Analysis

Quantify how individual parameters affect LCOE through local (Tornado) and global (Sobol) sensitivity analysis.

#### Tornado Analysis

One-at-a-time (OAT) parameter sweep [**[12]**](../reference/bibliography.md#ref12). Each input parameter is varied independently by a fixed percentage above and below its base value while all other parameters are held constant. The resulting LCOE swing for each parameter is plotted as a horizontal bar.

**Configuration:**

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| Variation | 5-30% | 10% | Percentage variation applied to each parameter around the base value. |

**Tornado diagram:** Horizontal bar chart ranking parameters by their impact on LCOE. Each bar spans from \(LCOE(\text{low})\) to \(LCOE(\text{high})\). The parameter with the widest bar has the greatest local influence on cost. A vertical line marks the baseline LCOE.

#### Sobol Sensitivity Indices

Global variance-based sensitivity analysis [**[11]**](../reference/bibliography.md#ref11). Unlike the Tornado approach, Sobol analysis accounts for interactions between parameters by decomposing the total output variance into contributions from individual parameters and their combinations.

**Configuration:**

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| Number of samples | 256-2048 | 512 | Base sample size for the Sobol sequence. Total model evaluations are \(N \times (2k + 2)\) where \(k\) is the number of parameters. |

Two indices are computed for each parameter:

| Index | Formula | Interpretation |
|-------|---------|----------------|
| First-order (\(S_1\)) | \(\displaystyle S_i = \frac{V_i}{V(Y)}\) | Fraction of output variance attributable to this parameter alone (see [OTEC-9](#first-order-sobol-index)). |
| Total-order (\(S_T\)) | \(\displaystyle S_{T_i} = 1 - \frac{V_{\sim i}}{V(Y)}\) | Fraction of output variance attributable to this parameter including all interactions with other parameters. |

**Sobol indices chart:** Grouped bar chart showing \(S_1\) (first-order) and \(S_T\) (total-order) for each parameter, ranked by total-order index.

**Export:** Tornado rankings and Sobol indices as CSV.

**Tips:**

- If a parameter's \(S_T\) is much larger than its \(S_1\), this indicates strong interaction effects with other parameters.
- Parameters with \(S_T < 0.01\) can be treated as fixed in subsequent analyses without meaningful loss of accuracy.
- Run the Tornado analysis first to identify the most important parameters quickly, then use Sobol analysis for a rigorous quantification that accounts for parameter interactions.


### Step 9: Off-Design Operation

Time-series analysis of OTEC plant performance under actual daily ocean temperature variations.

Unlike the on-design analysis (Step 3), which uses median temperatures to compute a single operating point, this step feeds the full daily temperature time series through the OTEX off-design model (`otex.plant.operation.otec_operation`). This captures the impact of seasonal and day-to-day temperature fluctuations on power output, efficiency, and LCOE.

**Input:** Daily warm and cold water temperature profiles from the Phase A analysis. When multiple grid cells are available, the profiles are averaged across cells.

**Run Off-Design:** Executes the OTEX off-design simulation in a background thread.

**Statistics panel:**

| Metric | Description |
|--------|-------------|
| Mean net power | Average daily net power output (kW and MW) |
| Mean net efficiency | Average thermodynamic cycle efficiency |
| Mean LCOE | Average daily LCOE (ct/kWh) |
| Actual CF | Ratio of mean net power to gross nominal power |
| Nominal comparison | Off-design mean vs. on-design nominal power and percentage |

**Charts (2x2 panel):**

1. **Monthly net power** -- Bar chart showing mean net power (MW) by month. Reveals the seasonal production profile under real temperature conditions.
2. **Monthly efficiency** -- Bar chart of mean net thermodynamic efficiency (%) by month.
3. **Monthly LCOE** -- Bar chart of mean LCOE (ct/kWh) by month. Months with lower \(\Delta T\) show higher LCOE.
4. **Power duration curve** -- Sorted daily net power from highest to lowest, showing what fraction of the year the plant operates at each power level. The area under this curve represents total annual energy production.

**Export:** Daily time series (day, net power in kW, net efficiency, LCOE in ct/kWh) as CSV.

**Tips:**

- Compare the off-design mean net power with the on-design nominal value. A ratio below 80% suggests the site's temperature variability significantly penalizes performance.
- The power duration curve shape indicates operational stability. A flat curve (small difference between maximum and minimum power) indicates consistent ocean thermal conditions.
- Days where the off-design model reports infeasible operation (NaN or positive net power) are excluded from statistics and marked in charts.


### Step 10: Cold Water Pipe & Transmission Sizing

Analyze the cold water pipe (CWP) dimensions, pumping losses, and submarine cable transmission efficiency.

The cold water pipe is typically the largest and most expensive single component of an OTEC plant. Its diameter determines the trade-off between friction losses (smaller pipe = higher losses) and structural cost (larger pipe = higher cost). This step allows parametric exploration of this trade-off.

**Configuration:**

| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| CWP depth | 100-5000 m | 1000 m | Cold water intake depth. Pre-filled from Step 2 maximum depth. |
| Distance to shore | 1-500 km | 20 km | Distance from the plant to the shore grid connection point. |
| Pipe diameter | 1-20 m | 10 m | Internal diameter of the CWP. |
| Gross power | 1-1000 MW | 136 MW | Plant gross power output. Pre-filled from Step 2. |
| Slope angle | 1-45° | 7° | CWP slope angle from horizontal. Steeper angles mean shorter pipes for the same depth. |

**Results panel:**

| Output | Units | Description |
|--------|-------|-------------|
| Pipe length | m | CWP length computed from depth and slope angle (see [OTEC-4a](#cwp-pipe-length)) |
| Pumping power | kW (MW) | Power consumed by the cold water circulation pump (see [OTEC-5](#pumping-power)) |
| Parasitic fraction | % | Pumping power as a fraction of gross power |
| Net power | kW (MW) | Gross power minus pumping power |
| Transmission efficiency | % | Cable transmission efficiency based on distance (AC below 50 km, DC above) |

**Diameter sweep chart:** Line plot of net delivered power (MW) vs. CWP diameter (m), with a vertical marker at the currently selected diameter. This reveals the optimal pipe diameter where increasing diameter no longer yields significant net power gains.

**Export:** Pipe analysis parameters and results as CSV.

**Tips:**

- The typical OTEC cold water flow rate is approximately 3-5 m³/s per MW of gross power. Larger plants require proportionally larger pipes.
- The Darcy friction factor is approximated as \(f = 0.015\) for large-diameter smooth pipes at high Reynolds numbers. For site-specific design, this should be refined using the Moody chart.
- The transmission efficiency model uses a quadratic fit for AC cables (distances up to 50 km) and a linear fit for DC cables (distances above 50 km).


### Step 11: Availability Profile Generation

Generate hourly capacity factor profiles for OTEC generators in the energy system model.

This final step bridges the OTEC resource assessment with the optimizer. It creates 8,760-hour (one year) availability profiles that can be assigned to OTEC generator objects in the system model, enabling the optimizer to dispatch OTEC generation with realistic temporal variation.

**Generator table:** Displays all OTEC-type generators found in the current system model (filtered by fuel type matching "otec", "ocean thermal", "thermal", or "ocean"). For each generator:

| Column | Description |
|--------|-------------|
| (checkbox) | Select/deselect for profile generation |
| Name | Generator display name |
| Unit key | Internal unit identifier |
| Node | Network node index |
| Position | Geographic coordinates (latitude, longitude) |
| Status | Processing status (Pending / Computing / Done / Skipped / Error) |

Generators without geographic coordinates are skipped.

**Profile generation:**

For each selected generator:

1. Find the nearest grid cell in the daily data (within 0.5° distance threshold).
2. Compute daily capacity factor from the temperature differential (see [OTEC-1a](#capacity-factor-from-temperature)).
3. Expand daily CF to hourly resolution (each daily value repeated 24 times).
4. Write the 8,760-value profile to a CSV file in the output directory.

If no grid cell is within range, a constant profile at the nominal availability is used.

**Output directory:** Defaults to `./availability/`. Each profile is saved as `{unit_key}_availability.csv` with shape (8760, n_nodes), where columns correspond to network nodes.

When generation completes, the generator objects in the system model are updated with the path to their availability files.

**Preview chart:** Monthly average capacity factor for each generated profile, plotted as colored lines on a single chart.

**Tips:**

- Ensure OTEC generators in the system model have correct geographic coordinates so the wizard can match them to the nearest ocean temperature grid cell.
- The 24-hour constant assumption within each day is reasonable for OTEC because ocean thermal conditions change slowly (diurnal SST variation is typically less than 1 °C).
- Generated profiles can be used directly by the optimizer's rolling-horizon dispatch. Set the generator's availability file path in the system configuration YAML.


---


## Mathematical Formulations


### Carnot Efficiency

The theoretical maximum (Carnot) efficiency of the OTEC cycle:

\[
\eta_C = 1 - \frac{T_{\text{cold}}}{T_{\text{hot}}} \tag{OTEC-1}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(\eta_C\) | -- | Carnot efficiency (dimensionless) |
| \(T_{\text{hot}}\) | K | Warm water temperature (surface intake, converted from °C: \(T_K = T_{°C} + 273.15\)) |
| \(T_{\text{cold}}\) | K | Cold water temperature (deep intake, converted from °C) |

For typical OTEC conditions (\(T_{\text{hot}} \approx 300\) K, \(T_{\text{cold}} \approx 278\) K), the Carnot efficiency is approximately 7.3%. Practical cycle efficiencies are lower due to irreversibilities.


### Actual Cycle Efficiency

The realized cycle efficiency accounting for thermodynamic irreversibilities:

\[
\eta_{\text{actual}} = \eta_C \times \eta_{\text{cycle}} \tag{OTEC-2}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(\eta_{\text{cycle}}\) | -- | Cycle efficiency factor (0-1), specific to the selected thermodynamic cycle (Rankine, Kalina, Uehara). Typically 0.4-0.6 for Rankine closed-cycle OTEC. |


### Net Power Output

The net electrical power delivered after subtracting all parasitic loads:

\[
P_{\text{net}} = P_{\text{gross}} - P_{\text{pump,CWP}} - P_{\text{pump,WWP}} - P_{\text{aux}} \tag{OTEC-3}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(P_{\text{gross}}\) | kW | Gross turbine-generator output |
| \(P_{\text{pump,CWP}}\) | kW | Cold water pipe pumping power |
| \(P_{\text{pump,WWP}}\) | kW | Warm water pipe pumping power |
| \(P_{\text{aux}}\) | kW | Auxiliary plant loads (controls, lighting, etc.) |

For large OTEC plants, parasitic pumping power typically consumes 20-30% of gross output, making pipe sizing (Step 10) critical to economic viability.


### CWP Pipe Length

The cold water pipe length from intake depth and slope angle:

\[
L_{\text{pipe}} = \frac{d}{\sin \theta} \tag{OTEC-4a}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(d\) | m | Cold water intake depth |
| \(\theta\) | rad | CWP slope angle from horizontal (minimum 1°) |


### CWP Friction Loss (Darcy-Weisbach)

The pressure drop along the cold water pipe due to friction:

\[
\Delta P = f \cdot \frac{L}{D} \cdot \frac{\rho v^2}{2} \tag{OTEC-4}
\]

Equivalently expressed as head loss:

\[
h_f = f \cdot \frac{L \cdot v^2}{2 \cdot D \cdot g} \tag{OTEC-4b}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(f\) | -- | Darcy friction factor (approximately 0.015 for large-diameter smooth pipes) |
| \(L\) | m | Pipe length |
| \(D\) | m | Internal pipe diameter |
| \(\rho\) | kg/m³ | Seawater density (1025 kg/m³) |
| \(v\) | m/s | Flow velocity (\(v = Q / A\), where \(Q\) is the volumetric flow rate and \(A = \pi D^2 / 4\)) |
| \(g\) | m/s² | Gravitational acceleration (9.81 m/s²) |
| \(h_f\) | m | Friction head loss |

The cold water flow rate is estimated as approximately 4 m³/s per MW of gross power, reflecting typical OTEC cycle heat balance requirements.


### Pumping Power

The electrical power consumed by the cold water circulation pump:

\[
P_{\text{pump}} = \frac{\rho \cdot g \cdot Q \cdot h_f}{\eta_{\text{pump}}} \tag{OTEC-5}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(Q\) | m³/s | Cold water volumetric flow rate |
| \(h_f\) | m | Friction head loss from [OTEC-4b](#cwp-friction-loss-darcy-weisbach) |
| \(\eta_{\text{pump}}\) | -- | Pump efficiency (default 0.80) |


### Levelized Cost of Energy (LCOE)

\[
LCOE = \frac{CRF(r, n) \times CAPEX + O\&M}{P_{\text{net}} \times CF \times 8760} \tag{OTEC-6}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(CRF\) | 1/yr | Capital Recovery Factor |
| \(CAPEX\) | $ | Total capital expenditure |
| \(O\&M\) | $/yr | Annual operation and maintenance cost |
| \(P_{\text{net}}\) | kW | Net power output |
| \(CF\) | -- | Capacity factor |
| \(8760\) | h/yr | Hours per year |

The LCOE represents the constant price per unit of energy that, over the plant lifetime, yields a net present value of zero [**[48]**](../reference/bibliography.md#ref48).


### Capital Recovery Factor (CRF)

\[
CRF(r, n) = \frac{r(1 + r)^n}{(1 + r)^n - 1} \tag{OTEC-7}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(r\) | -- | Discount rate (annual) |
| \(n\) | yr | Plant lifetime |

Converts a lump-sum present value into an equivalent uniform annual cost over \(n\) years at discount rate \(r\).


### Latin Hypercube Sampling (LHS)

\[
x_i^{(j)} \sim U\!\left(\frac{j-1}{N},\; \frac{j}{N}\right), \qquad j = 1, \ldots, N \tag{OTEC-8}
\]

For each input variable \(x_i\), the cumulative distribution is partitioned into \(N\) equal-probability strata. Exactly one sample is drawn from each stratum, and the samples are randomly permuted across strata to break correlations between variables while maintaining stratified coverage.

This ensures each region of the input space is sampled at least once, providing better coverage than simple random sampling for the same number of evaluations [**[12]**](../reference/bibliography.md#ref12).


### First-Order Sobol Index

\[
S_i = \frac{V_i}{V(Y)} = \frac{V\!\left[\mathbb{E}(Y \mid X_i)\right]}{V(Y)} \tag{OTEC-9}
\]

| Symbol | Description |
|--------|-------------|
| \(S_i\) | First-order Sobol sensitivity index for parameter \(i\) |
| \(V_i\) | Variance of the conditional expectation of the output given parameter \(i\) |
| \(V(Y)\) | Total unconditional variance of the output |
| \(Y\) | Model output (e.g., LCOE) |
| \(X_i\) | Input parameter \(i\) |

The first-order index \(S_i\) measures the fraction of total output variance that is explained by parameter \(i\) alone, without interactions. The sum of all first-order indices is at most 1; the gap to 1 is attributable to interaction effects [**[11]**](../reference/bibliography.md#ref11).

The total-order index is:

\[
S_{T_i} = 1 - \frac{V_{\sim i}}{V(Y)} \tag{OTEC-10}
\]

where \(V_{\sim i} = V\!\left[\mathbb{E}(Y \mid X_{\sim i})\right]\) is the variance explained by all parameters except \(i\). The difference \(S_{T_i} - S_i\) quantifies the interaction effects involving parameter \(i\).


### Transmission Efficiency

The submarine cable transmission efficiency model used by OTEX distinguishes between AC and DC transmission based on distance:

**AC transmission** (distance ≤ 50 km):

\[
\eta_{\text{trans}} = 0.979 - 10^{-6} \cdot d^2 - 9 \times 10^{-5} \cdot d \tag{OTEC-11a}
\]

**DC transmission** (distance > 50 km):

\[
\eta_{\text{trans}} = 0.964 - 8 \times 10^{-5} \cdot d \tag{OTEC-11b}
\]

where \(d\) is the distance to shore in km. The efficiency is bounded below by 0.01 to prevent numerical issues.


---


## Scripting

All wizard computations are available as Python functions for batch processing and Jupyter notebooks:

```python
from otex.config import get_default_config
from otex.plant.off_design_analysis import on_design_analysis
from otex.analysis import MonteCarloAnalysis, UncertaintyConfig
from otex.analysis import TornadoAnalysis, SobolAnalysis
from esfex.models.otec_models import (
    compute_carnot_efficiency,
    compute_daily_cf,
    compute_pipe_analysis,
    expand_daily_to_hourly,
)

# --- On-design analysis for a single site ---
import numpy as np

T_WW = 27.5   # warm water temperature (°C)
T_CW = 5.0    # cold water temperature (°C)

inputs = get_default_config(
    cycle_type="rankine_closed",
    fluid_type="ammonia",
    p_gross=-136000,       # kW (negative = output)
    installation="offshore",
).to_legacy_dict()

plant = on_design_analysis(
    T_WW=np.array([T_WW]),
    T_CW=np.array([T_CW]),
    inputs=inputs,
    cost_level="low_cost",
)
print(f"LCOE: {plant['LCOE'][0]:.2f} ct/kWh")
print(f"Net power: {abs(plant['p_net_nom'][0]):.0f} kW")

# --- Monte Carlo uncertainty ---
mc_config = UncertaintyConfig(n_samples=2000, seed=42, parallel=True)
mc = MonteCarloAnalysis(
    T_WW=T_WW, T_CW=T_CW,
    config=mc_config,
    p_gross=-136000,
    cost_level="low_cost",
)
results = mc.run(show_progress=True)
stats = results.compute_statistics()
print(f"LCOE mean: {stats['lcoe']['mean']:.2f} ct/kWh")
print(f"LCOE P5-P95: {stats['lcoe']['p5']:.2f} - {stats['lcoe']['p95']:.2f}")

# --- Sensitivity (Sobol) ---
sobol = SobolAnalysis(
    T_WW=T_WW, T_CW=T_CW,
    n_samples=512,
    p_gross=-136000,
    cost_level="low_cost",
)
sobol_results = sobol.run(output="lcoe", show_progress=True)
for i, name in enumerate(sobol_results.parameter_names):
    print(f"  {name}: S1={sobol_results.S1[i]:.3f}, ST={sobol_results.ST[i]:.3f}")

# --- Pipe sizing ---
pipe = compute_pipe_analysis(
    depth_m=1000, dist_shore_km=20,
    gross_power_kw=136000, pipe_diameter_m=10.0,
)
print(f"Pumping: {pipe.pumping_power_kw:.0f} kW ({pipe.pumping_fraction*100:.1f}%)")
print(f"Net delivered: {pipe.net_power_after_pumping_kw:.0f} kW")

# --- Carnot efficiency ---
eta = compute_carnot_efficiency(T_WW, T_CW)
print(f"Carnot efficiency: {eta*100:.2f}%")
```

See the [OTEX library documentation](https://pypi.org/project/otex/) for full API reference and additional analysis capabilities.
