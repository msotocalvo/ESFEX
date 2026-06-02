# Risk & Resilience Analysis

The risk and resilience module extends the [Stochastic Programming](stochastic-programming.md) formulation with explicit risk measures (CVaR, min-max regret), climate-adjusted resource modeling under Shared Socioeconomic Pathways, natural hazard fragility functions, and post-optimization resilience metrics. It enables planners to move beyond expected-cost minimization and quantify the robustness of capacity expansion plans against low-probability, high-consequence events. Notation follows the conventions in the [Formulation Overview](overview.md).

The module references the following international standards:

- **ISO 31000:2018** — Risk management principles and guidelines, including the risk assessment process (identification → analysis → evaluation → treatment) and the ALARP (As Low As Reasonably Practicable) framework for risk classification (§6.5).
- **ISO/IEC 31010:2019** — Risk assessment techniques, including Monte Carlo simulation (B.11), sensitivity analysis (B.10), copula-based dependence modeling (B.16), and Latin Hypercube Sampling (B.11.3).
- **ISO 22372:2025** — Referenced for its qualitative resilience framework (four adaptive capacities). Quantitative resilience metrics in ESFEX use LOLP, EENS, and the Panteli & Mancarella resilience index, which have established definitions in the power systems reliability literature.

---


## 1. Overview


The module operates in three complementary modes:

1. **Pre-optimization (risk-aware investment).** Hazard maps and fragility functions are translated into discrete damage scenarios with associated probabilities. These scenarios enter the stochastic Master Problem as additional second-stage recourse blocks, and the objective is augmented with a risk measure (CVaR or min-max regret) so that investment decisions hedge against catastrophic outcomes.

2. **During-optimization (stochastic with CVaR).** The two-stage stochastic program from [Stochastic Programming](stochastic-programming.md) is extended with auxiliary CVaR variables and constraints. The risk-averse objective balances expected cost against tail risk, producing investment plans that limit worst-case losses at a specified confidence level.

3. **Post-optimization (robustness evaluation).** Given a fixed investment plan \(I^*\) from the [Capacity Expansion](capacity-expansion.md) Master Problem, the system is re-dispatched under a large ensemble of Monte Carlo hazard scenarios. Resilience metrics — LOLP, EENS, resilience index (Panteli & Mancarella), and system average recovery time — are computed and reported alongside sensitivity analysis that identifies the most impactful uncertainties.

An interactive **Risk & Resilience Workbench** (GUI) provides a 3-step wizard: (1) Risk Analysis — auto-fetches hazard data from 7 public APIs, runs composite risk assessment with fragility functions, computes per-element risk coefficients, and displays failure probability heatmaps, EAL, IM exceedance curves, and sensitivity analysis; (2) Scenarios — climate projections (SSP pathways with demand adjustment) and hazard disaster scenarios for stochastic optimization; (3) Results & Export — summary, CSV/JSON/YAML export, and ISO 31000 §6.7 structured HTML reporting. All charts are exportable as PNG/SVG/PDF.

The foundation of all three modes is the scenario-based stochastic programming framework described in [Stochastic Programming](stochastic-programming.md). The present document defines the additional mathematical structures layered on top of that foundation.

---


## 2. Risk Measures


### 2.1 Expected Cost (Risk-Neutral)


The baseline stochastic objective minimizes expected cost across all scenarios, as defined in STOCH-OBJ of [Stochastic Programming](stochastic-programming.md):

\[
\min \; Z^{stoch} = C^{1st}(I) + \sum_{s=1}^{S} \pi_s \, C^{op}_s
\tag{RISK-0}
\]

| Symbol | Description |
|--------|-------------|
| \(C^{1st}(I)\) | First-stage investment cost (scenario-independent) |
| \(\pi_s\) | Probability of scenario \(s\) |
| \(C^{op}_s\) | Second-stage operational cost under scenario \(s\) |

This formulation is risk-neutral: it treats a scenario with moderate cost and a scenario with catastrophic cost symmetrically, weighted only by probability. For long-lived infrastructure exposed to tail risks, a risk-neutral objective may lead to underinvestment in resilience.

### 2.2 Conditional Value-at-Risk (CVaR)


The Conditional Value-at-Risk at confidence level \(\alpha\) is the expected loss in the worst \((1-\alpha)\) fraction of scenarios. Following Rockafellar and Uryasev [**[71]**](../reference/bibliography.md#ref71), CVaR admits a convex reformulation that can be embedded directly in a linear program.

**Definition.** Let \(L_s\) denote the total cost (loss) under scenario \(s\). The CVaR at confidence level \(\alpha \in (0,1)\) is:

\[
\mathrm{CVaR}_\alpha(L) = \min_{\eta} \left\{ \eta + \frac{1}{1 - \alpha} \sum_{s=1}^{S} \pi_s \, z_s \right\}
\tag{RISK-1}
\]

subject to the auxiliary constraints:

\[
z_s \geq L_s - \eta, \quad z_s \geq 0, \qquad \forall s \in \mathcal{S}
\tag{RISK-2}
\]

| Symbol | Domain | Description |
|--------|--------|-------------|
| \(\eta\) | \(\mathbb{R}\) | Value-at-Risk (VaR) threshold variable |
| \(z_s\) | \(\mathbb{R}_+\) | Excess loss above VaR in scenario \(s\) |
| \(\alpha\) | \((0,1)\) | Confidence level (e.g., 0.95 means worst 5%) |
| \(L_s\) | \(\mathbb{R}\) | Total system cost under scenario \(s\) |

At the optimum, \(\eta^*\) equals the Value-at-Risk \(\mathrm{VaR}_\alpha\), and \(z_s^* = \max(L_s - \eta^*, 0)\) captures the excess loss above VaR. The CVaR is then the conditional expectation of losses exceeding VaR.

**Risk-averse objective.** The risk-neutral expected cost is combined with CVaR through a convex combination controlled by the risk-aversion parameter \(\lambda\):

\[
\min \; Z^{risk} = (1 - \lambda) \sum_{s=1}^{S} \pi_s \, L_s \;+\; \lambda \left[ \eta + \frac{1}{1 - \alpha} \sum_{s=1}^{S} \pi_s \, z_s \right]
\tag{RISK-3}
\]

| Symbol | Range | Description |
|--------|-------|-------------|
| \(\lambda\) | \([0, 1]\) | Risk-aversion weight: 0 = risk-neutral, 1 = pure CVaR minimization |
| \(\alpha\) | \((0, 1)\) | CVaR confidence level; typical values: 0.90, 0.95, 0.99 |

When \(\lambda = 0\), the formulation reduces to expected-cost minimization (RISK-0). When \(\lambda = 1\), the optimizer minimizes only the tail risk, ignoring average performance. Intermediate values produce a Pareto trade-off between expected cost and tail-risk protection.

The formulation remains a linear program: \(\eta\) is a free variable, \(z_s\) are non-negative continuous variables, and the constraints (RISK-2) are linear. The number of additional variables is \(1 + S\) and the number of additional constraints is \(S\).

**Julia implementation note.** The function `create_risk_aware_master_problem(input, risk_config)` adds variable `η` (scalar), variables `z[s]` for each scenario, constraints (RISK-2), and modifies the objective to (RISK-3). The `risk_config.cvar_alpha` and `risk_config.cvar_lambda` parameters control \(\alpha\) and \(\lambda\) respectively.

**Post-optimization CVaR backend.** The Risk Workbench also computes CVaR for post-optimization risk assessment via Monte Carlo perturbation of hazard intensities. The `CompositeRiskAssessment` class generates \(N\) perturbed hazard maps (each IM multiplied by \(1 + \epsilon\), \(\epsilon \sim \mathcal{N}(0, \sigma^2)\)), computes the deterministic EAL for each sample, sorts the resulting distribution, and returns the risk-adjusted EAL:

\[
EAL_{risk} = (1 - \lambda) \cdot \mathbb{E}[EAL] + \lambda \cdot \mathrm{CVaR}_\alpha(EAL)
\tag{RISK-3b}
\]

where \(\mathrm{CVaR}_\alpha(EAL) = \mathbb{E}[EAL \mid EAL \geq \mathrm{VaR}_\alpha(EAL)]\) is the mean of the worst \((1-\alpha)\) fraction of the simulated EAL distribution.

### 2.3 Min-Max Regret


An alternative to CVaR for decision-makers who wish to minimize worst-case disappointment relative to hindsight-optimal decisions [**[75]**](../reference/bibliography.md#ref75). Let \(f(x, s)\) be the cost of decision \(x\) under scenario \(s\), and let \(f^*(s)\) be the optimal cost achievable if scenario \(s\) were known in advance:

\[
\min_{x \in \mathcal{X}} \; \max_{s \in \mathcal{S}} \left\{ f(x, s) - f^*(s) \right\}
\tag{RISK-4}
\]

| Symbol | Description |
|--------|-------------|
| \(f(x, s)\) | Total cost of investment plan \(x\) under scenario \(s\) |
| \(f^*(s)\) | Hindsight-optimal cost for scenario \(s\) (obtained by solving \(S\) deterministic problems) |
| \(\mathcal{X}\) | Feasible investment set |

Computing \(f^*(s)\) requires solving \(S\) independent deterministic Master Problems, one per scenario. With these values precomputed, the minimax problem is reformulated by introducing an epigraph variable \(\rho\):

\[
\min_{x \in \mathcal{X}, \, \rho} \; \rho \qquad \text{subject to} \quad f(x, s) - f^*(s) \leq \rho, \quad \forall s \in \mathcal{S}
\tag{RISK-5}
\]

| Symbol | Domain | Description |
|--------|--------|-------------|
| \(\rho\) | \(\mathbb{R}_+\) | Maximum regret (epigraph variable) |

This is a single LP (or MIP if the original problem is MIP) with \(S\) additional constraints linking the scenario costs to the epigraph variable.

**Post-optimization minimax regret backend.** For post-optimization analysis, the `CompositeRiskAssessment` class implements minimax regret by generating an ensemble of perturbed hazard maps and computing the worst-case EAL:

\[
EAL_{minimax} = \max_{s \in \{1, \ldots, N\}} EAL_s
\tag{RISK-5b}
\]

where each \(EAL_s\) is the deterministic Expected Annual Loss under a perturbed hazard intensity map \(s\). This provides the most conservative risk estimate — it is always at least as large as the CVaR-adjusted EAL.

### 2.4 Chance-Constrained Formulation


Chance constraints [**[72]**](../reference/bibliography.md#ref72) enforce that critical reliability requirements are met with high probability rather than in every scenario:

\[
\mathbb{P}\!\left( g(x, \xi) \leq 0 \right) \geq 1 - \varepsilon
\tag{RISK-6}
\]

| Symbol | Description |
|--------|-------------|
| \(g(x, \xi)\) | Constraint violation function (e.g., load shedding exceeds threshold) |
| \(\xi\) | Random vector representing uncertain parameters |
| \(\varepsilon\) | Allowed violation probability (e.g., 0.05 for 95% reliability) |

**Sample Average Approximation (SAA).** With a finite set of \(S\) scenarios, the chance constraint is approximated as:

\[
\frac{1}{S} \sum_{s=1}^{S} \mathbb{1}\!\left[ g(x, \xi_s) \leq 0 \right] \geq 1 - \varepsilon
\]

This is reformulated using binary indicator variables \(u_s \in \{0, 1\}\):

\[
g(x, \xi_s) \leq M \cdot (1 - u_s), \quad \forall s \in \mathcal{S}
\]

\[
\sum_{s=1}^{S} u_s \geq \lceil (1 - \varepsilon) \cdot S \rceil
\]

where \(M\) is a sufficiently large constant. The binary variables make this a MIP, increasing computational cost relative to the CVaR formulation.

---


## 3. Climate-Adjusted Resource Modeling


### 3.1 Shared Socioeconomic Pathways (SSPs)


Climate scenarios follow the IPCC AR6 framework of Shared Socioeconomic Pathways combined with Representative Concentration Pathways. Each SSP defines a trajectory for greenhouse gas concentrations and the resulting climate response.

| SSP Scenario | Radiative Forcing | Description | Approx. Warming by 2100 |
|-------------|-------------------|-------------|--------------------------|
| SSP1-2.6 | 2.6 W/m\(^2\) | Sustainability -- low challenges to mitigation and adaptation | +1.8 C |
| SSP2-4.5 | 4.5 W/m\(^2\) | Middle of the road -- moderate challenges | +2.7 C |
| SSP3-7.0 | 7.0 W/m\(^2\) | Regional rivalry -- high challenges to mitigation | +3.6 C |
| SSP5-8.5 | 8.5 W/m\(^2\) | Fossil-fueled development -- very high emissions | +4.4 C |

Each SSP translates into modified availability profiles for renewable generators, adjusted demand curves, and altered extreme-event frequencies. In the stochastic formulation, SSPs define scenarios \(\omega_s\) with associated probability weights \(\pi_s\).

### 3.2 Climate-Adjusted Availability Profiles


The pipeline for generating climate-adjusted capacity factors proceeds in three steps: (1) obtain downscaled climate projections from the NASA NEX-GDDP-CMIP6 dataset, (2) apply bias correction relative to historical observations, and (3) convert corrected meteorological variables to technology-specific capacity factors.

**Bias correction via quantile mapping.** Let \(F_{model}\) and \(F_{obs}\) denote the cumulative distribution functions of the climate model output and the historical observations, respectively. The corrected value is:

\[
x^{corrected} = F_{obs}^{-1}\!\left( F_{model}(x^{raw}) \right)
\tag{RISK-7}
\]

| Symbol | Description |
|--------|-------------|
| \(x^{raw}\) | Raw climate model output (e.g., GHI, wind speed, temperature) |
| \(F_{model}\) | CDF of the climate model for the historical reference period |
| \(F_{obs}\) | CDF of the observed historical data |
| \(F_{obs}^{-1}\) | Inverse CDF (quantile function) of the observations |

This ensures that the statistical distribution of the corrected projections matches the observed distribution while preserving the climate change signal (trend).

**Solar PV capacity factor.** The capacity factor under climate scenario \(s\) at time step \(t\) accounts for module temperature derating:

\[
CF^{solar}_s(t) = \eta_{module} \cdot \left(1 - \gamma_{temp} \cdot \left(T^{cell}_{s}(t) - 25\right)\right) \cdot \frac{GHI_s(t)}{GHI_{STC}}
\tag{RISK-8}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(\eta_{module}\) | -- | Module efficiency at Standard Test Conditions (STC) |
| \(\gamma_{temp}\) | 1/C | Temperature coefficient of power (typically 0.003--0.005 for c-Si) |
| \(T^{cell}_s(t)\) | C | Cell temperature under scenario \(s\) |
| \(GHI_s(t)\) | W/m\(^2\) | Global horizontal irradiance under scenario \(s\) |
| \(GHI_{STC}\) | W/m\(^2\) | Reference irradiance at STC (1000 W/m\(^2\)) |

Cell temperature is estimated from ambient temperature \(T_{amb}\) and irradiance using the NOCT model: \(T^{cell} = T_{amb} + (NOCT - 20) \cdot GHI / 800\).

**Wind capacity factor.** Wind power output is computed from the turbine power curve with air density correction:

\[
CF^{wind}_s(t) = P_{curve}\!\left(v_s(t)\right) \cdot \frac{\rho_s}{\rho_0}
\tag{RISK-9}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(P_{curve}(v)\) | -- | Normalized turbine power curve as a function of wind speed |
| \(v_s(t)\) | m/s | Hub-height wind speed under scenario \(s\) |
| \(\rho_s\) | kg/m\(^3\) | Air density under scenario \(s\) (temperature- and pressure-dependent) |
| \(\rho_0\) | kg/m\(^3\) | Reference air density (1.225 kg/m\(^3\) at sea level, 15 C) |

Air density decreases with rising temperature, reducing wind power output at a given wind speed by approximately 0.3--0.5% per degree Celsius of warming.

### 3.3 Temperature-Dependent Demand


Climate change affects electricity demand through heating and cooling loads. The demand under climate scenario \(s\) is modeled using heating degree days (HDD) and cooling degree days (CDD):

\[
D_s(t) = D_{base}(t) + \alpha_{heat} \cdot HDD_s(t) + \alpha_{cool} \cdot CDD_s(t)
\tag{RISK-10}
\]

\[
HDD_s(t) = \max\!\left(T_{base} - T_s(t),\; 0\right)
\tag{RISK-11a}
\]

\[
CDD_s(t) = \max\!\left(T_s(t) - T_{base},\; 0\right)
\tag{RISK-11b}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(D_{base}(t)\) | MW | Base demand profile (weather-normalized) |
| \(\alpha_{heat}\) | MW/degree-day | Heating demand sensitivity coefficient |
| \(\alpha_{cool}\) | MW/degree-day | Cooling demand sensitivity coefficient |
| \(T_s(t)\) | C | Ambient temperature under scenario \(s\) |
| \(T_{base}\) | C | Base temperature for HDD/CDD calculation (typically 18 C) |
| \(HDD_s(t)\) | degree-days | Heating degree days |
| \(CDD_s(t)\) | degree-days | Cooling degree days |

In tropical climates, \(\alpha_{heat} \approx 0\) and cooling dominates. The demand scaling factors in the YAML configuration (`demand_scale` per SSP) provide a simplified alternative when detailed temperature projections are unavailable.

### 3.4 Compound Climate Events


Compound events are defined as the simultaneous or sequential occurrence of multiple climate extremes whose combined impact exceeds the sum of individual effects. Examples include:

- **Heat wave + drought**: Increased cooling demand, reduced hydro inflows, reduced thermal plant cooling capacity.
- **Low wind + high demand**: Extended periods of low wind coinciding with winter demand peaks ("Dunkelflaute").
- **Storm surge + heavy rainfall**: Coastal flooding exacerbated by compound inundation.

Joint probability is modeled via copulas. Let \(U = F_1(X_1)\) and \(V = F_2(X_2)\) be the marginal CDFs of two hazard intensities. The joint CDF is:

\[
F(X_1, X_2) = C\!\left(F_1(X_1),\, F_2(X_2);\; \theta\right)
\]

where \(C(u, v; \theta)\) is a copula function parameterized by \(\theta\). Common choices include the Gaussian copula (for symmetric dependence) and the Clayton copula (for lower tail dependence, appropriate when extremes tend to co-occur). Compound event scenarios are generated by sampling from the joint distribution and included in the stochastic scenario set with appropriate probability weights.

---


## 4. Natural Hazard Risk Assessment


### 4.1 Hazard Characterization


Each natural hazard is characterized by an intensity measure (IM) that quantifies the severity of an event at a specific location.

| Hazard | Intensity Measure (IM) | Units | Typical Source |
|--------|----------------------|-------|----------------|
| Earthquake | Peak ground acceleration (PGA) | g | USGS ShakeMap, GEM |
| Tropical cyclone | 3-second gust wind speed | m/s | IBTrACS, STORM [**[80]**](../reference/bibliography.md#ref80) |
| Riverine flood | Inundation depth | m | WRI Aqueduct, Fathom 3.0 |
| Tsunami | Runup height | m | NOAA NCEI |
| Wildfire | Fire Weather Index (FWI) | -- | NASA FIRMS, GFWED |
| Volcanic ashfall | Tephra thickness | mm | Smithsonian GVP [**[83]**](../reference/bibliography.md#ref83) |
| Sea level rise | Inundation depth at SLR scenario | m | NASA AR6 SLR Tool |

Hazard maps provide the spatial distribution of IM at specified return periods (e.g., 50-year, 100-year, 500-year). Hazard intensities are evaluated at the **geographic coordinates of each individual element** (generator, battery, transmission endpoint), not at the node centroid. This ensures that equipment spread across a large node area receives location-specific hazard intensities. Elements without explicit coordinates fall back to their parent node's centroid.

### 4.2 Fragility Functions


Fragility functions relate the probability of reaching or exceeding a given damage state to the hazard intensity. Following common practice in structural and infrastructure engineering [**[77]**](../reference/bibliography.md#ref77), [**[78]**](../reference/bibliography.md#ref78), the lognormal CDF is used:

\[
P\!\left(DS \geq ds_i \mid IM = im\right) = \Phi\!\left(\frac{\ln(im) - \ln(\theta_i)}{\beta_i}\right)
\tag{RISK-12}
\]

| Symbol | Description |
|--------|-------------|
| \(\Phi(\cdot)\) | Standard normal cumulative distribution function |
| \(ds_i\) | Damage state \(i\) (Slight, Moderate, Extensive, Complete) |
| \(\theta_i\) | Median capacity (IM value at 50% exceedance probability) for damage state \(i\) |
| \(\beta_i\) | Logarithmic standard deviation (aleatory dispersion) for damage state \(i\) |
| \(\beta_{u,i}\) | Epistemic uncertainty (knowledge-based dispersion); total \(\beta_{total} = \sqrt{\beta_i^2 + \beta_{u,i}^2}\) |
| \(im\) | Observed or simulated hazard intensity |

**Epistemic uncertainty.** All built-in fragility curves include an epistemic uncertainty parameter \(\beta_u\) representing knowledge-based uncertainty in the fragility model parameters. Default values follow FEMA P-58 (2018) Table 3-1: \(\beta_u \approx 0.25\) for empirically derived curves (NHESS-2024, Suppasri-2013) and \(\beta_u \approx 0.30\) for analytically derived curves (PNNL-33587, Wilson-2012/2017). Proxy-derived and expert-judgment curves carry higher \(\beta_u\) (0.3–0.7). The total dispersion used in Monte Carlo uncertainty propagation is \(\beta_{total} = \sqrt{\beta^2 + \beta_u^2}\).

**Damage states.** Four damage states are defined with associated repair cost fractions:

| Damage State | Abbreviation | Typical Repair Cost (% of replacement) | Functional Capacity (%) |
|-------------|--------------|----------------------------------------|------------------------|
| Slight | DS1 | 2--10% | 90--100% |
| Moderate | DS2 | 10--30% | 50--90% |
| Extensive | DS3 | 30--70% | 10--50% |
| Complete | DS4 | 70--100% | 0--10% |

**Representative fragility parameters.** The following table provides representative values compiled from PNNL-33587 [**[78]**](../reference/bibliography.md#ref78) and the NHESS 2024 comprehensive fragility database [**[77]**](../reference/bibliography.md#ref77):

| Infrastructure | Hazard | Damage State | \(\theta_i\) | \(\beta_i\) |
|---------------|--------|-------------|--------------|-------------|
| Solar PV (ground-mounted) | Seismic (PGA) | Extensive | 0.50 g | 0.60 |
| Wind turbine (onshore) | Wind speed | Extensive | 55 m/s | 0.30 |
| Substation (HV) | Flood depth | Moderate | 0.30 m | 0.50 |
| Transmission tower | Wind speed | Extensive | 45 m/s | 0.40 |
| Diesel generator | Seismic (PGA) | Extensive | 0.70 g | 0.50 |
| Battery storage (BESS) | Flood depth | Moderate | 0.20 m | 0.40 |
| Distribution line | Wind speed | Extensive | 40 m/s | 0.35 |

### 4.3 Multi-Hazard Combination


When multiple hazards threaten the same infrastructure, their combined failure probability must account for possible dependencies between hazard occurrences.

**Independent hazards.** If hazard events are statistically independent, the combined probability of damage is:

\[
P_{total} = 1 - \prod_{h=1}^{H} \left(1 - P_h\right)
\tag{RISK-13}
\]

| Symbol | Description |
|--------|-------------|
| \(P_h\) | Probability of damage from hazard \(h\) individually |
| \(H\) | Number of hazard types considered |

**Copula-based dependence.** When hazards exhibit tail dependence (e.g., tropical cyclone wind and storm surge), a copula captures the joint exceedance structure.

*Clayton copula.* Appropriate for lower tail dependence [**[79]**](../reference/bibliography.md#ref79):

\[
C(u_1, \ldots, u_H;\; \theta) = \left(\sum_{i=1}^{H} u_i^{-\theta} - H + 1\right)^{-1/\theta}
\tag{RISK-14}
\]

| Symbol | Description |
|--------|-------------|
| \(u_i\) | Marginal CDF value for hazard \(i\), i.e., \(u_i = F_i(im_i)\) |
| \(\theta > 0\) | Copula dependence parameter (\(\theta \to 0\) gives independence) |

*Gaussian copula.* For symmetric dependence structures (ISO/IEC 31010 B.16), the implementation uses an equi-correlation Gaussian copula. Given individual failure probabilities \(P_1, \ldots, P_H\), the joint failure probability is computed by transforming to the normal space:

\[
z_i = -\Phi^{-1}(P_i), \qquad i = 1, \ldots, H
\tag{RISK-14b}
\]

where the survival quantiles \(z_i\) define a multivariate normal distribution with equi-correlation matrix \(\Sigma\):

\[
\Sigma_{ij} = \begin{cases} 1 & i = j \\ \rho & i \neq j \end{cases}
\tag{RISK-14c}
\]

The combined failure probability is then:

\[
P_{total} = 1 - \Phi_H(\mathbf{z};\; \boldsymbol{0}, \Sigma)
\tag{RISK-14d}
\]

| Symbol | Description |
|--------|-------------|
| \(\Phi^{-1}(\cdot)\) | Inverse standard normal CDF (probit function) |
| \(\rho\) | Equi-correlation coefficient (default: 0.3) |
| \(\Phi_H(\cdot)\) | Multivariate normal CDF of dimension \(H\) |

The Gaussian copula typically yields a lower combined failure probability than the independence assumption when \(\rho > 0\), because positive correlation means hazards are less "additive" — if one has already occurred, others are partially accounted for. This is implemented via `scipy.stats.multivariate_normal.cdf()`.

**MCDA weighted overlay.** For screening-level composite risk assessment (e.g., site selection), a weighted linear combination of normalized risk scores is used:

\[
R_{composite} = \sum_{h=1}^{H} w_h \cdot R_h, \qquad \sum_{h=1}^{H} w_h = 1
\tag{RISK-15}
\]

| Symbol | Description |
|--------|-------------|
| \(R_h\) | Normalized risk score for hazard \(h\), \(R_h \in [0, 1]\) |
| \(w_h\) | Weight reflecting relative importance of hazard \(h\) |

### 4.4 Expected Annual Loss (EAL)


The Expected Annual Loss integrates losses over all possible event intensities, weighted by their annual exceedance frequencies. Using a discrete approximation over return periods:

\[
EAL = \sum_{k=1}^{K} \left(\frac{1}{RP_k} - \frac{1}{RP_{k+1}}\right) \cdot L_k
\tag{RISK-16}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(RP_k\) | years | Return period for the \(k\)-th intensity level |
| \(L_k\) | \$ | Expected loss at the intensity corresponding to \(RP_k\) |
| \(K\) | -- | Number of discrete return period bins |

The term \(1/RP_k - 1/RP_{k+1}\) is the annual probability of an event with intensity between the \(k\)-th and \((k+1)\)-th levels. By convention, \(1/RP_{K+1} = 0\) for the most extreme bin. The EAL provides a single annualized metric for comparing risk across sites or investment alternatives.

### 4.5 Composite Risk Index


A per-element composite risk index (CRI) aggregates hazard, exposure, and vulnerability at each element's geographic location following the INFORM methodology with geometric aggregation:

\[
CRI_n = \left(\sum_{h=1}^{H} w_h \cdot H_{h,n}\right)^{\alpha_H} \cdot E_n^{\alpha_E} \cdot V_n^{\alpha_V}
\tag{RISK-17}
\]

| Symbol | Range | Description |
|--------|-------|-------------|
| \(H_{h,n}\) | \([0, 10]\) | Hazard score for hazard \(h\) at node \(n\) |
| \(E_n\) | \([0, 10]\) | Exposure score at node \(n\) (installed capacity, population served) |
| \(V_n\) | \([0, 10]\) | Vulnerability score at node \(n\) (equipment age, redundancy) |
| \(\alpha_H, \alpha_E, \alpha_V\) | \((0, 1]\) | Dimension exponents (typically \(1/3\) for equal weighting) |
| \(w_h\) | \([0, 1]\) | Hazard-specific weight within the hazard dimension |

The geometric aggregation ensures that a zero score in any dimension drives the composite index toward zero, reflecting the principle that risk requires the simultaneous presence of hazard, exposure, and vulnerability.

---


## 5. Hazard Scenario Generation


### 5.1 From Risk Map to Optimization Scenarios


Continuous hazard distributions must be discretized into a finite set of scenarios suitable for the stochastic programming framework. The conversion pipeline proceeds as follows:

**Monte Carlo sampling.** For each sample \(i = 1, \ldots, N_{MC}\):

1. **Draw hazard intensity** from the return-period distribution at each node: \(im_{h,n}^{(i)} \sim F_{IM}^{-1}(U)\), where \(U \sim \mathrm{Uniform}(0,1)\).
2. **Evaluate fragility** for each component at each node using (RISK-12): \(P^{(i)}_{damage,g,n} = \Phi\!\left(\frac{\ln(im^{(i)}_{h,n}) - \ln(\theta_g)}{\beta_g}\right)\).
3. **Draw binary damage state** via Bernoulli sampling: component \((g,n)\) fails if \(\mathrm{Bernoulli}(P^{(i)}_{damage,g,n}) = 1\).
4. **Record outage pattern** as a binary vector indicating which components are damaged, forming scenario \(\omega_i\).

The resulting \(N_{MC}\) scenarios are then reduced to a tractable set using the methods described in Section 5.2.

### 5.2 Scenario Reduction


The full Monte Carlo ensemble is typically too large for direct inclusion in the stochastic program. Scenario reduction techniques [**[81]**](../reference/bibliography.md#ref81) select a representative subset that approximates the original distribution in the Kantorovich (Wasserstein) distance sense.

**Forward selection.** Starting from an empty set \(\mathcal{S}' = \emptyset\), iteratively add the scenario that most reduces the Kantorovich distance between the reduced set and the full set:

\[
\min_{\mathcal{S}' \subset \mathcal{S},\; |\mathcal{S}'| = S'} \; \sum_{s \in \mathcal{S} \setminus \mathcal{S}'} \pi_s \cdot \min_{s' \in \mathcal{S}'} d(s, s')
\]

**Backward reduction.** Starting from the full set \(\mathcal{S}' = \mathcal{S}\), iteratively remove the scenario whose removal least increases the Kantorovich distance. The probability of removed scenarios is redistributed to their nearest retained neighbor.

Both methods produce a reduced scenario set with updated probability weights suitable for the stochastic Master Problem.

### 5.3 Importance Sampling for Rare Events


Low-probability, high-impact events (e.g., 1-in-1000-year earthquake, Category 5 cyclone) are poorly represented in standard Monte Carlo sampling. Importance sampling uses a tilted distribution \(q(\xi)\) that oversamples the tail region:

\[
\mathbb{E}_p[f(\xi)] = \mathbb{E}_q\!\left[\frac{p(\xi)}{q(\xi)} \cdot f(\xi)\right] \approx \frac{1}{N} \sum_{i=1}^{N} w_i \cdot f(\xi_i), \qquad w_i = \frac{p(\xi_i)}{q(\xi_i)}
\]

| Symbol | Description |
|--------|-------------|
| \(p(\xi)\) | Original (physical) probability distribution of hazard intensity |
| \(q(\xi)\) | Tilted (importance) sampling distribution |
| \(w_i\) | Importance weight for sample \(i\) |

The tilted distribution concentrates probability mass on extreme events, improving the statistical efficiency of tail risk estimation. After sampling, the weights \(w_i\) correct for the bias introduced by oversampling.

For integration with the stochastic program, the importance-sampled scenarios enter with probabilities proportional to their corrected weights, \(\pi_s = w_s / \sum_{s'} w_{s'}\).

### 5.4 Latin Hypercube Sampling (LHS)


Latin Hypercube Sampling (ISO/IEC 31010 B.11.3) provides stratified coverage of the probability space with fewer samples than standard Monte Carlo. For \(d\) dimensions (high-risk nodes) and \(N\) samples, the \([0,1]^d\) hypercube is partitioned into \(N^d\) strata, and exactly one sample is drawn from each stratum along each dimension.

The `ScenarioGenerator` implements LHS via `scipy.stats.qmc.LatinHypercube`:

1. Identify high-risk nodes (those with nonzero composite risk) as dimensions.
2. Generate \(N\) LHS samples in \([0,1]^d\) with stratified coverage.
3. For each sample vector \(\mathbf{u} = (u_1, \ldots, u_d)\), trigger a hazard event at node \(i\) when:
\[
u_i > 1 - P_{composite,i}
\]
where \(P_{composite,i}\) is the composite failure probability at node \(i\).
4. Apply damage fractions from fragility functions and compute capacity reductions.
5. Add a baseline "no disaster" scenario with probability proportional to the joint survival probability.
6. Normalize scenario probabilities to sum to 1.

LHS provides better coverage of tail events than crude Monte Carlo for the same sample count, making it suitable for moderate-dimensional problems (\(d \leq 20\)). For higher dimensions, importance sampling or standard Monte Carlo remain preferable.

---


## 6. Integration with Optimization Model


### 6.1 Hazard-Aware Capacity Constraints


Risk integrates with the Master Problem at two levels:

**Level 1 — Per-element risk coefficient (deterministic).** Each generator, battery, and investment technology receives a geographic risk coefficient \(\rho_{g} \in [0,1]\) that permanently derates its effective capacity in the capacity adequacy constraint. This coefficient represents the expected fraction of capacity *not* lost to natural hazards, computed from the multi-hazard composite failure probability at the element's **own geographic location** (not the node centroid):

\[
\rho_{g,n} = 1 - P_{combined}(\text{complete failure} \mid \text{location}_n, \text{type}_g)
\tag{RISK-17b}
\]

The capacity adequacy expression in the Master Problem (CUM-1 through CUM-4) is modified to:

\[
C^{eff}_{g,n,y} = C^{cumul}_{g,n,y} \cdot \rho_{g,n} \cdot \alpha^{cc}_{g,n} \cdot (1 - \delta_{g,n})^{age}
\tag{RISK-18a}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(\rho_{g}\) | \([0, 1]\) | Risk coefficient: geographic hazard derating for element \(g\) at its location |
| \(\alpha^{cc}_{g,n}\) | \([0, 1]\) | Capacity credit (minimum availability for renewables, 1.0 for dispatchable) |

This applies identically to existing units and new investment technologies. A coastal solar PV with \(\rho = 0.82\) contributes only 82% of its rated power to system adequacy, incentivizing the optimizer to either invest more at risky locations or prefer safer ones.

**Level 2 — Hazard scenarios (stochastic).** Discrete disaster events with per-element damage fractions enter the stochastic Master Problem as scenarios:

\[
C^{avail}_{g,n,y,h} = C^{cumul}_{g,n,y} \cdot \left(1 - \phi^{damage}_{g,n,h}\right)
\tag{RISK-18b}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(C^{cumul}_{g,n,y}\) | MW | Cumulative installed capacity of generator \(g\) at node \(n\) in year \(y\) |
| \(\phi^{damage}_{g,n,h}\) | \([0, 1]\) | Damage fraction for generator \(g\) at node \(n\) under hazard scenario \(h\) |
| \(C^{avail}_{g,n,y,h}\) | MW | Available capacity after damage |

The damage fraction is derived from fragility functions (RISK-12) evaluated at the specific hazard intensity of scenario \(h\). Level 1 captures expected risk (continuous derating); Level 2 captures extreme events (discrete scenarios). Both are complementary.

### 6.2 Recovery Dynamics


Following a disaster event in year \(y_0\), damaged components are progressively restored over a recovery period. The available capacity in subsequent years accounts for both damage and recovery:

\[
C^{avail}_{g,n,y_0+k,h} = C^{cumul}_{g,n,y_0+k} \cdot \min\!\left(1,\; \frac{k}{\tau^{recov}_h}\right)
\tag{RISK-19}
\]

for \(k = 0, 1, \ldots, \tau^{recov}_h\), where:

| Symbol | Units | Description |
|--------|-------|-------------|
| \(k\) | years | Years elapsed since the disaster event |
| \(\tau^{recov}_h\) | years | Recovery time for hazard scenario \(h\) |

At \(k = 0\), the capacity is fully damaged (multiplied by 0); at \(k = \tau^{recov}_h\), it is fully restored. The linear recovery model is a first-order approximation; more detailed models can use S-curve or exponential recovery profiles.

### 6.3 Insurance Cost Integration


Risk-exposed components incur additional operational expenditure in the form of insurance premiums or self-insurance reserves. The annual insurance cost is proportional to the replacement cost and the composite risk index:

\[
C^{insur}_{g,n} = \mu^{insur} \cdot C^{repl}_{g,n} \cdot CRI_n
\tag{RISK-20}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(\mu^{insur}\) | -- | Insurance rate (fraction of replacement cost per year) |
| \(C^{repl}_{g,n}\) | \$ | Full replacement cost of generator \(g\) at node \(n\) |
| \(CRI_n\) | -- | Composite risk index at node \(n\) (from RISK-17) |

This cost is added to the annual operational cost in the objective function, providing an incentive for the optimizer to avoid concentrating investments in high-risk locations.

### 6.4 VOLL-Differentiated Load Shedding


The standard formulation uses a single Value of Lost Load (VOLL) penalty for all unserved demand. Risk-aware planning differentiates by sector to reflect heterogeneous economic impacts [**[84]**](../reference/bibliography.md#ref84):

\[
C^{ls} = \sum_{n \in \mathcal{N}} \sum_{t \in \mathcal{T}} \sum_{k \in \mathcal{K}_{sector}} VOLL_k \cdot LS_{n,t,k}
\tag{RISK-21}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(VOLL_k\) | \$/MWh | Value of lost load for sector \(k\) |
| \(LS_{n,t,k}\) | MW | Load shedding at node \(n\), time \(t\), sector \(k\) |
| \(\mathcal{K}_{sector}\) | -- | Set of demand sectors (residential, commercial, industrial, critical) |

Typical VOLL values:

| Sector | VOLL (\$/MWh) | Rationale |
|--------|--------------|-----------|
| Residential | 5,000 | Inconvenience, food spoilage |
| Commercial | 25,000 | Lost revenue, productivity |
| Industrial | 15,000 | Process interruption, material waste |
| Critical (hospitals, water) | 100,000 | Life safety, public health |

The sectoral VOLL creates a shedding priority: the optimizer will shed residential load before industrial, and industrial before critical, reflecting societal preferences.

---


## 7. Resilience Metrics


The following metrics quantify system resilience from different perspectives. They are computed post-optimization from the dispatch results under hazard scenarios.

### 7.1 Loss of Load Probability (LOLP)


LOLP measures the fraction of time periods in which any load shedding occurs:

\[
LOLP = \frac{1}{T} \sum_{t=1}^{T} \mathbb{1}\!\left[\sum_{n} LS_{n,t} > 0\right]
\tag{RISK-22}
\]

| Symbol | Description |
|--------|-------------|
| \(T\) | Total number of time periods evaluated |
| \(LS_{n,t}\) | Load shedding at node \(n\), time \(t\) (MW) |
| \(\mathbb{1}[\cdot]\) | Indicator function (1 if condition is true, 0 otherwise) |

A LOLP of 0.001 means load is shed in 0.1% of hours, equivalent to approximately 8.76 hours per year.

### 7.2 Expected Energy Not Supplied (EENS)


EENS is the probability-weighted total energy not served across all scenarios:

\[
EENS = \sum_{s=1}^{S} \pi_s \sum_{t=1}^{T} \sum_{n \in \mathcal{N}} LS_{n,t,s} \cdot \Delta t
\tag{RISK-23}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(EENS\) | MWh | Expected annual energy not supplied |
| \(\pi_s\) | -- | Probability of scenario \(s\) |
| \(LS_{n,t,s}\) | MW | Load shedding at node \(n\), time \(t\), scenario \(s\) |
| \(\Delta t\) | hours | Time step duration |

### 7.3 Resilience Index (Panteli & Mancarella)


The resilience index [**[73]**](../reference/bibliography.md#ref73), [**[74]**](../reference/bibliography.md#ref74) quantifies the ability of the system to absorb, adapt to, and recover from a disruptive event. It is defined as the ratio of actual system performance to ideal (undisturbed) performance over the event duration:

\[
R = 1 - \frac{A_{lost}}{A_{ideal}}
\tag{RISK-24}
\]

where:

\[
A_{lost} = \int_{0}^{T_{event}} \left(F_{ideal}(t) - F_{actual}(t)\right) dt
\]

\[
A_{ideal} = \int_{0}^{T_{event}} F_{ideal}(t) \, dt
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(F_{ideal}(t)\) | MW | Ideal system performance (total demand met) |
| \(F_{actual}(t)\) | MW | Actual system performance (demand met minus load shedding) |
| \(A_{lost}\) | MWh | Area between the ideal and actual performance curves |
| \(A_{ideal}\) | MWh | Area under the ideal performance curve |
| \(T_{event}\) | hours | Duration from event onset to full recovery |
| \(R\) | \([0,1]\) | Resilience index (1 = fully resilient, 0 = total failure) |

The performance curve \(F_{actual}(t)\) exhibits four characteristic phases:

1. **Pre-disturbance** (\(F = F_{ideal}\)): Normal operation, no degradation.
2. **Degradation**: System performance drops as components fail due to the hazard event.
3. **Sustained outage**: Performance reaches its minimum; repair has not yet begun or is ongoing.
4. **Recovery**: Performance progressively returns to normal as components are repaired or replaced.

### 7.4 System Average Recovery Time


SART is the capacity-weighted average recovery time across all damaged components:

\[
SART = \frac{\sum_{g \in \mathcal{G}_{dam}} \tau^{recov}_g \cdot \bar{P}_g}{\sum_{g \in \mathcal{G}_{dam}} \bar{P}_g}
\tag{RISK-25}
\]

| Symbol | Units | Description |
|--------|-------|-------------|
| \(\mathcal{G}_{dam}\) | -- | Set of generators damaged by the hazard event |
| \(\tau^{recov}_g\) | hours | Recovery time for generator \(g\) |
| \(\bar{P}_g\) | MW | Rated power of generator \(g\) |

SART provides a single metric for comparing the recovery speed of different investment plans. Plans that invest in geographically distributed and hazard-resistant technologies will have lower SART.

### 7.5 Relationship to ISO 22372

ISO 22372:2025 defines a qualitative framework for urban and infrastructure resilience with four adaptive capacities (anticipatory, absorptive, adaptive, restorative). The standard provides conceptual definitions but does not prescribe quantitative formulas for computing these capacities. Quantitative resilience assessment in ESFEX relies on the metrics defined in Sections 7.1–7.4 (LOLP, EENS, Resilience Index, SART), which have established mathematical definitions in the power systems reliability literature.

### 7.6 Redundancy Index


The N-1 redundancy index quantifies the fraction of nodes that have sufficient backup capacity:

\[
N1_{redundancy} = \frac{1}{|\mathcal{N}|} \sum_{n \in \mathcal{N}} \mathbb{1}\!\left[ C_{total,n} - C_{max\_unit,n} \geq D_{peak,n} \right]
\tag{RISK-25f}
\]

A score of 1.0 means every node can survive the loss of its largest generating unit without load shedding.

---


## 8. Post-Optimization Stress Testing


### 8.1 Scenario Enumeration


Given an optimal investment plan \(I^*\) from the Master Problem, post-optimization stress testing fixes the investment decisions and re-dispatches the system under each hazard scenario:

**For each scenario \(s = 1, \ldots, S_{stress}\):**

\[
\min_{P, E, L, \ldots} \; C^{op}_s \qquad \text{subject to operational constraints with } C^{avail}_{g,n,y,s}
\]

where:

- Investment variables are fixed: \(I = I^*\) (no new capacity additions allowed).
- Generator capacities are reduced according to (RISK-18) using the damage fractions of scenario \(s\).
- Recovery dynamics (RISK-19) apply for multi-year scenarios.
- All other operational constraints (power balance, generator limits, battery dynamics, transmission) remain active.

This produces a distribution of operational costs and reliability outcomes across the stress scenarios.

### 8.2 Monte Carlo Aggregation


The stress test results are aggregated using standard risk statistics:

\[
NPV_{risk} = \frac{1}{S_{MC}} \sum_{s=1}^{S_{MC}} NPV_s
\tag{RISK-26}
\]

| Statistic | Formula | Description |
|-----------|---------|-------------|
| Expected NPV | \(\mathbb{E}[NPV] = \sum_s \pi_s \cdot NPV_s\) | Probability-weighted average net present value |
| VaR at \(\alpha\) | \(VaR_\alpha = \mathrm{quantile}(NPV_{samples},\, \alpha)\) | Cost not exceeded with probability \(\alpha\) |
| CVaR at \(\alpha\) | \(CVaR_\alpha = \mathbb{E}\!\left[NPV \mid NPV \geq VaR_\alpha\right]\) | Expected cost in the worst \((1-\alpha)\) fraction |
| Maximum loss | \(\max_s NPV_s\) | Worst-case cost across all scenarios |
| Standard deviation | \(\sigma = \sqrt{\mathbb{E}[(NPV - \mathbb{E}[NPV])^2]}\) | Spread of the cost distribution |

The risk profile is visualized as a histogram of \(NPV_s\) values overlaid with VaR and CVaR markers.

### 8.3 Monte Carlo Uncertainty Propagation (ISO/IEC 31010 B.11)


Beyond stress testing of fixed investment plans, the Risk Workbench performs full Monte Carlo uncertainty propagation (ISO/IEC 31010 B.11) through the fragility-to-EAL pipeline. The `CompositeRiskAssessment.monte_carlo_eal()` method jointly perturbs two independent uncertainty sources:

**Epistemic uncertainty** (knowledge uncertainty in fragility parameters):

\[
\beta'_i = \beta_i \cdot (1 + \epsilon^{ep}_i), \qquad \epsilon^{ep}_i \sim \mathcal{N}(0, \sigma^2_{ep})
\tag{RISK-26b}
\]

where \(\beta_i\) is the logarithmic standard deviation of the fragility curve and \(\sigma_{ep}\) is the epistemic coefficient of variation (default: 0.15).

**Aleatory uncertainty** (natural variability in hazard intensity):

\[
IM'_{h,n} = IM_{h,n} \cdot (1 + \epsilon^{al}_{h,n}), \qquad \epsilon^{al}_{h,n} \sim \mathcal{N}(0, \sigma^2_{al})
\tag{RISK-26c}
\]

where \(\sigma_{al}\) is the aleatory coefficient of variation (default: 0.20).

For each of \(N_{MC}\) samples (default: 1000), the full fragility evaluation and EAL computation pipeline is executed with perturbed parameters. The output is a `MonteCarloRiskResult` containing:

| Statistic | Formula | Description |
|-----------|---------|-------------|
| \(\mathbb{E}[EAL]\) | Mean of samples | Expected annual loss |
| \(\sigma_{EAL}\) | Standard deviation | Uncertainty spread |
| \(EAL_{p5}, EAL_{p50}, EAL_{p95}\) | Percentiles | Confidence bounds |
| \(\mathrm{VaR}_\alpha\) | \(\alpha\)-quantile | Value-at-Risk |
| \(\mathrm{CVaR}_\alpha\) | Mean above VaR | Conditional Value-at-Risk |
| Dominant uncertainty | Variance decomposition | Epistemic vs. aleatory identification |

The dominant uncertainty source is identified by comparing two restricted Monte Carlo runs: one perturbing only fragility \(\beta\) (epistemic) and one perturbing only hazard IM (aleatory). The source with higher variance in EAL is flagged as dominant.

### 8.4 OAT Sensitivity Analysis (ISO/IEC 31010 B.10)


Complementing the global Sobol analysis (Section 8.5), the Risk Workbench provides a One-At-a-Time (OAT) sensitivity sweep for rapid tornado diagram construction. The `sensitivity_sweep()` method varies five key parameters across their plausible ranges while holding others at baseline values:

| Parameter | Low Bound | High Bound | Description |
|-----------|-----------|------------|-------------|
| CVaR \(\alpha\) | 0.80 | 0.99 | Confidence level |
| CVaR \(\lambda\) | 0.0 | 1.0 | Risk-aversion weight |
| Fragility \(\beta\) scale | 0.7 | 1.3 | Multiplier on all fragility dispersions |
| Hazard IM scale | 0.8 | 1.2 | Multiplier on all intensity measures |
| Combination method | independent | copula / MCDA | Multi-hazard combination approach |

For each parameter, the total EAL is computed at both bounds, producing a swing \(\Delta EAL = |EAL_{high} - EAL_{low}|\). The results feed the existing `SensitivityTornadoChart`, which ranks parameters by their impact on the output — the parameter with the largest swing has the greatest influence on risk estimates.

### 8.5 Sobol Sensitivity Indices


Variance-based global sensitivity analysis [**[11]**](../reference/bibliography.md#ref11), [**[12]**](../reference/bibliography.md#ref12) decomposes the variance of a model output \(Y\) (e.g., total system cost) into contributions from individual input factors \(X_i\) and their interactions.

**First-order Sobol index** (main effect of \(X_i\)):

\[
S_i = \frac{V\!\left[\mathbb{E}\!\left[Y \mid X_i\right]\right]}{V[Y]}
\tag{RISK-27}
\]

**Total-order Sobol index** (main effect plus all interactions involving \(X_i\)):

\[
S_{Ti} = 1 - \frac{V\!\left[\mathbb{E}\!\left[Y \mid X_{\sim i}\right]\right]}{V[Y]}
\tag{RISK-28}
\]

| Symbol | Description |
|--------|-------------|
| \(V[Y]\) | Total variance of the model output |
| \(V[\mathbb{E}[Y \mid X_i]]\) | Variance of the conditional expectation of \(Y\) given \(X_i\) |
| \(X_{\sim i}\) | All input factors except \(X_i\) |
| \(S_i\) | First-order index: fraction of variance explained by \(X_i\) alone |
| \(S_{Ti}\) | Total-order index: fraction of variance involving \(X_i\) in any interaction |

The difference \(S_{Ti} - S_i\) quantifies the importance of interactions involving \(X_i\). Input factors are typically: hazard intensities, fragility parameters, demand growth, fuel prices, technology costs, and climate scenario parameters. The Sobol analysis guides scenario design by identifying which uncertainties most affect investment decisions.

---


## 9. Risk Evaluation & ALARP Classification (ISO 31000 §6.5)


### 9.1 ALARP Framework


Following ISO 31000:2018 §6.5, risk evaluation classifies each node's risk level against predefined criteria to determine whether risk treatment is required. The ALARP (As Low As Reasonably Practicable) framework defines three regions:

1. **Negligible region** — Risk is broadly acceptable; no action required.
2. **Tolerable (ALARP) region** — Risk should be reduced if reasonably practicable. Split into "tolerable low" and "tolerable high" sub-bands.
3. **Intolerable region** — Risk is unacceptable; mandatory risk treatment required.

### 9.2 Risk Criteria Configuration


Risk criteria are configured through `RiskCriteriaConfig` with the following thresholds:

| Criterion | Threshold Field | Default | Unit |
|-----------|----------------|---------|------|
| EAL negligible | `eal_negligible` | 1,000 | \$/year |
| EAL tolerable | `eal_tolerable` | 50,000 | \$/year |
| EAL intolerable | `eal_intolerable` | 500,000 | \$/year |
| Composite risk low | `composite_risk_low` | 0.01 | probability |
| Composite risk medium | `composite_risk_medium` | 0.05 | probability |
| Composite risk high | `composite_risk_high` | 0.15 | probability |

### 9.3 Classification Algorithm


The `evaluate_risk_criteria()` function classifies each node based on both EAL and composite risk:

\[
\text{class}(n) = \begin{cases}
\text{intolerable} & \text{if } EAL_n \geq T_{intolerable} \text{ or } R_n \geq T_{high} \\
\text{tolerable\_high} & \text{if } EAL_n \geq T_{tolerable} \text{ or } R_n \geq T_{medium} \\
\text{tolerable\_low} & \text{if } EAL_n \geq T_{negligible} \text{ or } R_n \geq T_{low} \\
\text{negligible} & \text{otherwise}
\end{cases}
\]

Each classification carries an `action_required` flag (true for `intolerable` and `tolerable_high`) and a justification string explaining the basis. In the Risk Workbench GUI, table rows are color-coded:

| Classification | Color | Action |
|---------------|-------|--------|
| Negligible | Green | Monitor only |
| Tolerable low | Yellow | Reduce if practicable |
| Tolerable high | Orange | Active risk reduction |
| Intolerable | Red | Mandatory treatment |

---


## 10. ISO 31000 §6.7 Structured Reporting


The `ISOReportGenerator` class produces ISO 31000 §6.7 compliant structured HTML reports with embedded CSS, suitable for stakeholder communication and audit documentation. The report contains nine sections:

1. **Executive Summary** — Top risks by EAL, total portfolio EAL, overall resilience score, number of intolerable nodes.
2. **Context & Scope** — Geographic scope, number of nodes, stakeholders, applicable standards, risk criteria thresholds.
3. **Risk Identification** — Hazard types assessed, number of hazard maps, data sources, exposed infrastructure inventory.
4. **Risk Analysis** — Per-element EAL breakdown, fragility model parameters, uncertainty characterization (epistemic vs. aleatory), combination method used.
5. **Risk Evaluation** — ALARP classification table with color-coded risk bands, nodes requiring action, justification for each classification.
6. **Resilience Assessment** — ISO 22372 four-capacity scores, LOLP, EENS, resilience index, SART, performance curve summary.
7. **Risk Treatment Recommendations** — Auto-generated recommendations based on classification: intolerable nodes receive mandatory mitigation actions, tolerable nodes receive cost-benefit guidance.
8. **Monitoring & Review** — Key parameters to track over time, recommended review schedule, triggers for re-assessment.
9. **Appendices** — Methodology description, complete data source inventory, sensitivity analysis results, Monte Carlo statistics.

The report is generated from the `ExportApplyPanel` in the Risk Workbench via the "ISO Report (HTML)" button and saved to a user-selected file path.

---


## 11. Data Sources


### 11.1 Multi-Source Hazard Fetchers


The Risk Workbench integrates 13 data sources across 7 hazard types through a unified fetcher architecture. Each fetcher implements automatic coordinate-based queries, return-period interpolation, and local caching:

| Hazard Type | Source | Fetcher Class | IM | Return Periods |
|-------------|--------|--------------|-----|----------------|
| Multi-hazard screening | ThinkHazard! (GFDRR) | `ThinkHazardFetcher` | Classification (1-4) | N/A |
| Composite risk | INFORM Risk (DRMKC) | `INFORMRiskFetcher` | Risk index (0-10) | N/A |
| Earthquake | USGS PSHA | `USGSEarthquakeFetcher` | PGA (g) | 50, 100, 250, 500, 1000, 2500 yr |
| Earthquake | GEM Hazard Mosaic | `GEMHazardFetcher` | PGA (g) | 50, 100, 500, 1000, 2500 yr |
| Earthquake | OpenQuake Engine | `OpenQuakeFetcher` | PGA (g) | User-defined |
| Tropical cyclone | IBTrACS (NOAA) | `IBTrACSCycloneFetcher` | Wind speed (m/s) | 25, 50, 100, 250, 500 yr |
| Tropical cyclone | STORM Synthetic | `STORMCycloneFetcher` | Wind speed (m/s) | 50, 100, 500, 1000, 10000 yr |
| Riverine flood | WRI Aqueduct 4.0 | `AqueductFloodFetcher` | Depth (m) | 5, 10, 25, 50, 100, 250, 500, 1000 yr |
| Wildfire | NASA FIRMS (NRT) | `NASAFIRMSFetcher` | Fire density (fires/km\(^2\)) | Empirical |
| Wildfire | GFWED | `GFWEDWildfireFetcher` | FWI index | Climatological |
| Tsunami | NOAA NCEI | `NOAATsunamiFetcher` | Runup height (m) | Empirical |
| Volcanic ashfall | Smithsonian GVP | `SmithsonianVolcanoFetcher` | VEI + distance decay | Statistical |
| Sea level rise | NASA AR6 SLR | `NASASLRFetcher` | Inundation (m) | SSP-based projections |

All fetchers produce `HazardIntensityMap` objects with standardized fields: hazard type, source name, IM values per return period, geographic coordinates, and confidence intervals. Results are cached in `~/.cache/esfex/hazards/` with a configurable TTL.

### 11.2 Hazard Database Summary


| Hazard | Primary Source | Format | Resolution | Access |
|--------|---------------|--------|------------|--------|
| Multi-hazard screening | ThinkHazard! (GFDRR) | REST API (JSON) | Admin division | Free |
| Composite risk index | INFORM Risk (DRMKC) | Excel / CSV | National / subnational | Free |
| Earthquake | USGS ComCat + GEM | REST API + NRML | ~1 km PGA grid | Free |
| Tropical cyclone | IBTrACS + STORM [**[80]**](../reference/bibliography.md#ref80) | CSV / NetCDF | 10 km wind return periods | Free |
| Riverine flood | WRI Aqueduct / Fathom 3.0 | GeoTIFF | 1 km / 30 m | Free (non-commercial) |
| Wildfire | NASA FIRMS + GFWED | REST API + NetCDF | 375 m / ~50 km | Free (API key) |
| Tsunami | NOAA NCEI | REST API | Point data | Free |
| Volcanic ashfall | Smithsonian GVP [**[83]**](../reference/bibliography.md#ref83) | Excel download | Point data | Free |
| Sea level rise | NASA AR6 SLR Tool | NetCDF / Zarr | Global grid | Free |
| Climate projections | NEX-GDDP-CMIP6 | NetCDF | 0.25 deg, daily | Free (AWS) |

### 11.3 Fragility Function Sources


| Source | Coverage | Infrastructure Types | Format |
|--------|----------|---------------------|--------|
| NHESS 2024 (Nirandjan et al.) [**[77]**](../reference/bibliography.md#ref77) | Global, 1,510+ curves | Energy, transport, water | Open-access database |
| PNNL-33587 [**[78]**](../reference/bibliography.md#ref78) | United States | Power generation, T&D | PDF report |
| FEMA HAZUS-MH | United States | Utilities, lifelines | Esri geodatabase |
| GEM Vulnerability DB | Global, ~500 functions | Buildings (seismic) | NRML XML |

### 11.4 Python Libraries


| Library | Purpose |
|---------|---------|
| `climada` | Unified hazard-exposure-vulnerability framework |
| `openquake.engine` | Probabilistic seismic hazard analysis + fragility evaluation |
| `libcomcat` | USGS earthquake catalog access |
| `cdsapi` | Copernicus Climate Data Store (ERA5, CMIP6, GloFAS) |
| `xarray` | NetCDF / GRIB climate data processing |
| `SALib` | Sobol sensitivity analysis (Saltelli estimator) |
| `scipy.stats.qmc` | Latin Hypercube Sampling for stratified scenario generation |
| `scipy.stats.multivariate_normal` | Gaussian copula for multi-hazard dependence modeling |

---


## 12. Configuration


The risk and resilience module is configured through the `risk` section of the YAML configuration file:

```yaml
risk:
  enabled: true
  risk_measure: cvar          # Options: expected, cvar, minimax_regret
  cvar_alpha: 0.05            # CVaR confidence level (worst 5%)
  cvar_lambda: 0.5            # Risk-aversion weight (0 = risk-neutral, 1 = pure CVaR)

  risk_criteria:              # ALARP thresholds (ISO 31000 §6.5)
    eal_negligible: 1000      # $/year — below this, risk is broadly acceptable
    eal_tolerable: 50000      # $/year — above this, risk reduction needed
    eal_intolerable: 500000   # $/year — above this, mandatory action
    composite_risk_low: 0.01  # probability threshold for "low" band
    composite_risk_medium: 0.05
    composite_risk_high: 0.15

  climate_scenarios:
    ssp245:
      probability: 0.4
      availability_suffix: ssp245
      demand_scale: {2030: 1.05, 2040: 1.12, 2050: 1.20}
      temperature_delta: {2030: 0.8, 2040: 1.2, 2050: 1.6}
    ssp370:
      probability: 0.35
      availability_suffix: ssp370
      demand_scale: {2030: 1.08, 2040: 1.18, 2050: 1.35}
      temperature_delta: {2030: 1.0, 2040: 1.6, 2050: 2.2}
    ssp585:
      probability: 0.25
      availability_suffix: ssp585
      demand_scale: {2030: 1.10, 2040: 1.25, 2050: 1.50}
      temperature_delta: {2030: 1.2, 2040: 2.0, 2050: 3.0}

  hazard_scenarios:
    nankai_earthquake:
      probability: 0.03
      year_of_occurrence: 5
      affected_nodes: [0, 1]
      affected_generators: [solar_pv_0, wind_0]
      damage_fraction: {solar_pv_0: 0.8, wind_0: 0.3}
      recovery_years: 3

    typhoon_cat5:
      probability: 0.01
      year_of_occurrence: 8
      affected_nodes: [0, 1, 2]
      affected_generators: [wind_0, wind_1]
      damage_fraction: {wind_0: 1.0, wind_1: 0.6}
      recovery_years: 2

  voll_by_sector:
    residential: 5000          # $/MWh
    commercial: 25000
    industrial: 15000
    critical: 100000

  insurance_rate_hazard: 0.008  # Annual insurance rate (fraction of replacement cost)
```

**Entry points:**

| Function / Class | Mode | Description |
|----------|------|-------------|
| `create_risk_aware_master_problem(input, risk_config)` | Pre-optimization | Builds the stochastic Master Problem with CVaR variables and hazard-aware capacity constraints |
| `evaluate_risk_robustness(h5_path, risk_config)` | Post-optimization | Reads a solved HDF5 results file, stress-tests the investment plan against hazard scenarios, and computes resilience metrics |
| `CompositeRiskAssessment` | Workbench | Multi-hazard risk assessment with CVaR, minimax regret, copula combination, Monte Carlo propagation, and OAT sensitivity |
| `evaluate_risk_criteria(profiles, criteria)` | Workbench | ALARP classification of nodes against configurable thresholds |
| `ResilienceAnalyzer` | Workbench | ISO 22372 resilience metrics: LOLP, EENS, resilience index, SART, 4 adaptive capacities |
| `ScenarioGenerator` | Workbench | Hazard scenario generation via Monte Carlo, importance sampling, or Latin Hypercube Sampling |
| `ISOReportGenerator` | Workbench | ISO 31000 §6.7 compliant structured HTML report generation |

---


## 13. Relationship to Other Formulations


The risk and resilience module is compatible with all other ESFEX components:

| Feature | Compatibility | Notes |
|---------|--------------|-------|
| [Capacity Expansion](capacity-expansion.md) | Full | Hazard-aware capacity constraints (RISK-18) modify cumulative expressions |
| [Stochastic Programming](stochastic-programming.md) | Extension | CVaR objective (RISK-3) adds variables and constraints to the stochastic formulation |
| [Operational Dispatch](operational-dispatch.md) | Full | Stress testing re-dispatches with damaged capacities |
| [DC Power Flow](dc-power-flow.md) | Full | Transmission damage modeled via reduced line ratings |
| [AC OPF](ac-power-flow.md) | Full | Post-hazard AC feasibility verification |
| [Primary Energy](primary-energy.md) | Full | Fuel supply disruption modeled as hazard scenario |
| MGA/SPORES | Sequential | Near-optimal alternatives evaluated under hazard scenarios |

---


## References

The CVaR formulation follows Rockafellar and Uryasev [**[71]**](../reference/bibliography.md#ref71). The stochastic programming foundation is detailed in Birge and Louveaux [**[72]**](../reference/bibliography.md#ref72). Resilience metrics follow the framework of Panteli and Mancarella [**[73]**](../reference/bibliography.md#ref73), [**[74]**](../reference/bibliography.md#ref74). Robust optimization theory is covered by Ben-Tal et al. [**[75]**](../reference/bibliography.md#ref75). MILP investment decomposition under uncertainty follows Munoz et al. [**[76]**](../reference/bibliography.md#ref76). Fragility functions are compiled from Nirandjan et al. [**[77]**](../reference/bibliography.md#ref77) and PNNL-33587 [**[78]**](../reference/bibliography.md#ref78). Multi-hazard assessment methodology follows Watson and Etemadi [**[79]**](../reference/bibliography.md#ref79). Synthetic tropical cyclone data is from Bloemendaal et al. [**[80]**](../reference/bibliography.md#ref80). Scenario tree reduction follows Heitsch and Romisch [**[81]**](../reference/bibliography.md#ref81). Distributionally robust optimization via the Wasserstein metric is treated by Esfahani and Kuhn [**[82]**](../reference/bibliography.md#ref82). Volcanic hazard impacts on critical infrastructure are reviewed by Wilson et al. [**[83]**](../reference/bibliography.md#ref83). Sector-differentiated VOLL estimates follow the Brattle Group study [**[84]**](../reference/bibliography.md#ref84). Sobol sensitivity analysis follows Sobol [**[11]**](../reference/bibliography.md#ref11) and Saltelli et al. [**[12]**](../reference/bibliography.md#ref12).

**ISO standards compliance:**

- ISO 31000:2018 — Risk management: Guidelines (risk assessment process, ALARP framework §6.5, structured reporting §6.7)
- ISO/IEC 31010:2019 — Risk assessment techniques (Monte Carlo B.11, sensitivity analysis B.10, copula B.16, LHS B.11.3)
- ISO 22372:2025 — Security and resilience: Urban resilience (four adaptive capacities framework)

See the [full bibliography](../reference/bibliography.md) for complete citation details.
