# Financial Analysis Wizard

System-level post-optimization financial assessment of energy system investments. Access via **Workflows > Financial Analysis**.

The wizard evaluates the economic viability of an optimized generation and storage portfolio over its planning horizon. Starting from the HDF5 results produced by the optimizer, it computes discounted cash flows, investment return metrics, per-technology cost breakdowns, and risk-adjusted project value. The analysis follows standard project finance methodology [**[68]**](../reference/bibliography.md#ref68) and system cost metrics used in energy planning [**[48]**](../reference/bibliography.md#ref48) [**[64]**](../reference/bibliography.md#ref64).

The wizard is organized in two phases:

- **Phase A (Steps 1-4)**: Economic Overview. Configure financial assumptions, examine the cost structure of the optimized system, compare technology economics, and analyze market conditions.
- **Phase B (Steps 5-8)**: Deep Financial Analysis. Build pro-forma cash flows, compute investment return and bankability metrics, run sensitivity and risk analyses, and export reports.

All computations use the `esfex.models.financial_analysis` engine, which can also be used independently for scripting and batch analysis — see [API Reference](../api/models-financial-analysis.md).


---


## Phase A — Economic Overview


### Step 1: Load Results & Assumptions

Load completed simulation results and configure the financial assumptions that govern the analysis.

**Results file selection:**

- **HDF5 file picker**: Browse for a `results_*.h5` file produced by `Orchestrator.run()`. The file must contain at least one year of results.
- **System selector**: If the file contains multiple systems (multi-system optimization), choose which system to analyze.

**Financial assumptions form:**

Parameters are organized in four groups. All fields have default values and can be adjusted to reflect the specific market, regulatory environment, and project characteristics.

| Group | Parameters | Description |
|-------|-----------|-------------|
| **Capital structure** | Debt fraction, cost of debt, cost of equity, debt tenor | Defines how the project is financed. A typical utility-scale project uses 60-80% debt financing at 4-7% interest with 12-20 year tenor. The equity return expectation (cost of equity) reflects the risk premium investors require. |
| **Tax & depreciation** | Tax rate, depreciation method, depreciation years, ITC rate, PTC rate | Governs the tax treatment of the investment. Straight-line depreciation spreads the deduction evenly; MACRS front-loads it. Investment Tax Credits (ITC) reduce CAPEX in year 0; Production Tax Credits (PTC) provide per-MWh revenue. |
| **Revenue** | PPA price, PPA escalation, capacity payment, REC price | Revenue assumptions. When PPA price is 0, the wizard uses nodal electricity prices from the optimization as the revenue basis. Capacity payments (\$/MW-year) and Renewable Energy Certificate prices (\$/MWh) provide additional revenue streams. |
| **Environmental** | Carbon price, carbon price escalation | Carbon pricing applied to avoided CO2 emissions. Revenue is computed as the difference between first-year emissions and each subsequent year's emissions, valued at the escalating carbon price. |

**Load & Analyze**: Runs the complete financial computation in a background thread. A progress indicator is shown while the engine reads HDF5 data and computes all metrics.

When the analysis completes, all subsequent steps are populated with results automatically.

**Tips:**

- Start with default values for an initial assessment, then refine assumptions to match the specific regulatory and market context.
- The discount rate is the single most influential parameter on NPV. Set it to the project's WACC or the investor's required rate of return.
- When PPA price is 0, the analysis uses the marginal prices computed by the optimizer (shadow prices of the power balance constraint). These reflect the system's willingness to pay for additional generation at each hour.


### Step 2: Cost Decomposition

Breakdown of the system's total cost into its constituent components over the planning horizon.

Three views are available via a tab selector:

**NPV Waterfall:**

Horizontal waterfall chart showing how each cost and revenue category contributes to the net project value. Starting from zero, positive bars (costs) are added sequentially — CAPEX, fuel, O&M, penalties — followed by negative bars (revenue, tax benefits, salvage value). The final bar shows the net NPV.

The NPV of each component is computed as:

\[
NPV_k = \sum_{y=0}^{N-1} \frac{C_{k,y}}{(1 + r)^y} \tag{FA-1}
\]

where \(C_{k,y}\) is the cash flow for component \(k\) in year \(y\), \(r\) is the discount rate, and \(N\) is the number of planning years.

**Annual Stacked Bar:**

Year-by-year stacked bar chart showing the magnitude of each cost component. Useful for identifying temporal patterns: when capital investment occurs, how fuel costs evolve with technology transitions, and how penalty costs decrease as the system expands.

**Cost Pie Chart:**

Proportional breakdown of total discounted costs by category. Reveals the dominant cost driver — typically fuel for fossil-heavy systems, or CAPEX for renewable-dominated systems.


### Step 3: Technology Economics

Per-technology financial comparison. This step links the physical optimization results (generation, capacity, dispatch) to their economic consequences.

**Metrics table:**

One row per generator and battery (including virtual units from technology investments). Columns:

| Column | Units | Description |
|--------|-------|-------------|
| Name | — | Technology name |
| Type | — | Generator or battery |
| Installed | MW | Cumulative installed capacity |
| Generation | GWh | Total energy produced over the planning horizon |
| Capacity Factor | % | Average ratio of actual output to nameplate capacity |
| LCOE | \$/MWh | Levelized cost of energy (see [Formulas](#levelized-cost-of-energy-lcoe)) |
| VALCOE | \$/MWh | Value-adjusted LCOE. Negative values indicate the technology is profitable at market prices |
| Revenue | M\$ | Total discounted revenue |
| Fuel Cost | M\$ | Total discounted fuel cost |
| O&M Cost | M\$ | Total discounted operation and maintenance cost |
| ROI | % | Return on investment: (revenue − total cost) / CAPEX |

**Bubble chart:**

Technologies plotted with capacity factor on the x-axis and LCOE on the y-axis. Bubble size is proportional to installed capacity (MW). Technologies in the bottom-right quadrant (high capacity factor, low LCOE) represent the most cost-effective investments.

**Revenue vs. cost bar chart:**

Side-by-side comparison of total discounted revenue and total discounted cost per technology. Technologies where revenue exceeds cost are net contributors to project value.


### Step 4: Market Analysis

Energy market context derived from the optimization's electricity price signals.

**Price duration curve:**

Sorted system-average electricity prices from highest to lowest, showing the fraction of hours at each price level. The shape reveals the market structure:

- A flat curve indicates stable prices (baseload-dominated system).
- A steep left tail indicates price spikes (peaking demand, low reserve margin).
- A long right tail near zero indicates frequent periods of renewable surplus.

**Cumulative NPV chart:**

Year-by-year trajectory of cumulative discounted net cash flow. The x-intercept (where the curve crosses zero) indicates the discounted payback period. The final value equals the project NPV.


---


## Phase B — Deep Financial Analysis


### Step 5: Cash Flows

Pro-forma financial statements showing the annual flow of funds through the project.

**Cash flow table:**

A scrollable table with one row per planning year and the following columns:

| Column | Description |
|--------|-------------|
| Year | Planning year |
| Revenue | Total energy revenue + capacity payments + REC + carbon credits (\$) |
| Fuel Cost | Fuel consumption cost (\$) |
| O&M Cost | Fixed and variable operation & maintenance (\$) |
| Insurance | Annual insurance (\$) |
| CAPEX | Capital expenditures in this year (\$) |
| Depreciation | Tax depreciation deduction (\$) |
| Tax | Corporate income tax (\$) |
| PTC Benefit | Production Tax Credit revenue (\$) |
| Debt Service | Principal + interest payment (\$) |
| Net Cash Flow | Revenue − costs − tax + incentives (\$) |
| Equity Cash Flow | Net cash flow − debt service (\$) |
| Cumulative NPV | Running sum of discounted net cash flows (\$) |
| DSCR | Debt Service Coverage Ratio (dimensionless) |

**Cumulative NPV chart:**

Same trajectory as Step 4 with additional annotation of the payback year.

**DSCR timeline:**

Bar chart of annual Debt Service Coverage Ratio with a horizontal reference line at 1.2×, which is the minimum threshold typically required by project lenders. The DSCR is computed as:

\[
DSCR_y = \frac{CFADS_y}{DS_y} \tag{FA-2}
\]

where \(CFADS_y\) is the cash flow available for debt service in year \(y\) (revenue minus operating expenses minus tax, before debt payments) and \(DS_y\) is the scheduled debt service (principal + interest).

Years with DSCR below 1.2 are highlighted, indicating periods of potential financial stress.


### Step 6: Investment Metrics

Dashboard of key return and bankability metrics displayed as large-format metric cards for rapid assessment.

| Metric | Formula | Interpretation |
|--------|---------|----------------|
| **NPV** | \(\displaystyle NPV = \sum_{y=0}^{N-1} \frac{CF_y}{(1+r)^y}\) | Net present value of all project cash flows [**[68]**](../reference/bibliography.md#ref68). Positive NPV indicates the project creates value above the required rate of return. |
| **Project IRR** | Solve \(\displaystyle \sum_{y=0}^{N-1} \frac{CF_y}{(1+IRR)^y} = 0\) | Discount rate that sets NPV to zero. An IRR above the WACC indicates a viable project. Solved via bisection. |
| **MIRR** | \(\displaystyle MIRR = \left(\frac{FV^+}{|PV^-|}\right)^{1/N} - 1\) | Modified IRR [**[69]**](../reference/bibliography.md#ref69). Uses the cost of debt as finance rate and cost of equity as reinvestment rate. More robust than IRR for non-conventional cash flow patterns. |
| **Discounted Payback** | Year where cumulative NPV ≥ 0 | Time required for discounted cumulative cash flows to recover the initial investment. Projects with shorter payback carry lower risk. |
| **WACC** | \(D \cdot r_d (1-t) + (1-D) \cdot r_e\) | Weighted average cost of capital. Blends the after-tax cost of debt and cost of equity according to the capital structure. |
| **Min DSCR** | \(\min_y DSCR_y\) | Minimum annual DSCR during the debt tenor. Lenders typically require ≥ 1.20. |
| **System LCOE** | \(\displaystyle \frac{\sum_y C_y^{\text{cost}} / (1+r)^y}{\sum_y E_y / (1+r)^y}\) | Levelized cost of energy [**[48]**](../reference/bibliography.md#ref48) [**[64]**](../reference/bibliography.md#ref64). Total discounted cost divided by total discounted generation. |

A supplementary chart plots NPV as a function of discount rate from 0% to 25%. The x-intercept of this curve is the project IRR.


### Step 7: Sensitivity & Risk

Quantifies how financial outcomes respond to changes in assumptions and assesses the probability distribution of project value under uncertainty.

#### Sensitivity Analysis

One-at-a-time (OAT) parameter sweeps [**[12]**](../reference/bibliography.md#ref12). Each selected assumption variable is varied independently over a range while all other variables are held at their base values.

**Configuration:**

| Setting | Default | Description |
|---------|---------|-------------|
| Variable selection | 6 variables | Checkboxes to choose which financial assumptions to sweep. Available: discount rate, PPA price, carbon price, debt fraction, cost of debt, tax rate. |
| Range | ±30% | Variation range around the base value. |
| Points | 11 | Number of evaluation points per variable. |

**Run Sensitivity**: Executes sweeps in a background thread.

**Tornado diagram:**

Horizontal bar chart ranking variables by their impact on NPV. Each bar spans from \(NPV(\text{low})\) to \(NPV(\text{high})\). The variable with the widest bar has the greatest influence on project value. This identifies which assumptions most need refinement or hedging.

**Spider plot:**

Overlay of NPV-vs-parameter-value curves for all selected variables on a single chart. The slope of each curve at the base-case point measures the local sensitivity:

\[
S_k = \frac{\partial NPV}{\partial x_k} \cdot \frac{x_k}{NPV} \tag{FA-3}
\]

Steeper curves indicate higher sensitivity. Variables with nearly flat curves can be treated as fixed in subsequent analyses without significant loss of accuracy.

#### Monte Carlo Simulation

Probabilistic risk analysis through random sampling from user-specified distributions [**[70]**](../reference/bibliography.md#ref70).

**Configuration:**

| Setting | Default | Description |
|---------|---------|-------------|
| Sample count | 1000 | Number of Monte Carlo iterations. Higher counts give smoother distributions but take longer. |
| Distributions | Normal for discount rate and PPA price; uniform for tax rate | Per-variable probability distributions. Supported: `normal(μ, σ)`, `uniform(a, b)`, `triangular(a, b)` with mode at the base value. |

**Run Monte Carlo**: Executes sampling in a background thread.

**NPV histogram:**

Distribution of NPV outcomes with vertical annotations:

- **Mean**: Expected project value.
- **VaR (5%)**: Value-at-Risk at 5% — the NPV value exceeded with 95% probability. Represents the near-worst-case outcome.
- **CVaR (5%)**: Conditional VaR (Expected Shortfall) — the mean NPV of the worst 5% of outcomes. Captures tail risk more completely than VaR.

\[
VaR_\alpha = F^{-1}_{NPV}(\alpha), \qquad CVaR_\alpha = \mathbb{E}[NPV \mid NPV \leq VaR_\alpha] \tag{FA-4}
\]

**IRR histogram:**

Distribution of IRR outcomes, showing the probability of achieving the target return.

**Tips:**

- Start with sensitivity analysis to identify the most impactful variables, then focus the Monte Carlo distributions on those variables.
- Use 1,000 samples for initial exploration and 5,000+ for final analysis.
- The fraction of NPV samples above zero gives the probability that the project is economically viable.


### Step 8: Report Export

Generate a summary report and export all analysis results.

**Executive summary:**

Auto-generated text summarizing key findings:

- Project viability assessment (NPV sign, IRR vs. WACC comparison).
- Dominant cost components and most cost-effective technologies.
- Most sensitive financial assumptions (from tornado analysis).
- Probability of positive NPV (from Monte Carlo, if available).
- Key risk factors and recommended hedging strategies.

The text is editable before export, allowing the user to add project-specific context or modify conclusions.

**Export options:**

| Format | Content |
|--------|---------|
| **CSV** | Cash flow table, sensitivity sweep data, per-technology metrics. One CSV file per data table. |
| **TXT** | Executive summary as a plain text file. |


---


## Mathematical Formulations


### Weighted Average Cost of Capital (WACC)

\[
WACC = D \cdot r_d \cdot (1 - t) + (1 - D) \cdot r_e \tag{FA-5}
\]

| Symbol | Description |
|--------|-------------|
| \(D\) | Debt fraction (0-1) |
| \(r_d\) | Cost of debt (annual interest rate) |
| \(t\) | Corporate tax rate |
| \(r_e\) | Cost of equity (required return) |

The after-tax formulation accounts for the tax deductibility of interest payments.


### Capital Recovery Factor (CRF)

\[
CRF(r, n) = \frac{r(1+r)^n}{(1+r)^n - 1} \tag{FA-6}
\]

Converts a lump-sum present value into an equivalent uniform annual cost over \(n\) years at discount rate \(r\). Used to annualize capital expenditures for LCOE computation and to compute constant-annuity debt service.


### Net Present Value (NPV)

\[
NPV = \sum_{y=0}^{N-1} \frac{CF_y}{(1+r)^y} \tag{FA-7}
\]

where \(CF_y\) is the net cash flow in year \(y\) (revenue − operating expenses − tax − debt service + incentives) and \(r\) is the discount rate (typically WACC or the investor's required rate of return).


### Internal Rate of Return (IRR)

\[
\sum_{y=0}^{N-1} \frac{CF_y}{(1+IRR)^y} = 0 \tag{FA-8}
\]

Solved numerically via bisection over \([-0.5, \, 5.0]\) with tolerance \(\epsilon = 10^{-6}\). The project IRR uses pre-debt cash flows; the equity IRR uses post-debt cash flows.


### Modified Internal Rate of Return (MIRR) [**[69]**](../reference/bibliography.md#ref69)

\[
MIRR = \left(\frac{FV^+}{|PV^-|}\right)^{1/N} - 1 \tag{FA-9}
\]

where

\[
FV^+ = \sum_{\{y : CF_y > 0\}} CF_y \cdot (1 + r_{\text{reinvest}})^{N-y}, \qquad PV^- = \sum_{\{y : CF_y < 0\}} \frac{CF_y}{(1 + r_{\text{finance}})^y}
\]

The finance rate is the cost of debt and the reinvestment rate is the cost of equity. MIRR avoids the multiple-root and reinvestment-rate assumptions inherent in standard IRR.


### Levelized Cost of Energy (LCOE) [**[48]**](../reference/bibliography.md#ref48) [**[64]**](../reference/bibliography.md#ref64)

**System LCOE:**

\[
LCOE_{\text{sys}} = \frac{\sum_{y=0}^{N-1} (C_y^{\text{fuel}} + C_y^{\text{O\&M}} + C_y^{\text{capex}}) / (1+r)^y}{\sum_{y=0}^{N-1} E_y / (1+r)^y} \tag{FA-10}
\]

**Per-technology LCOE:**

\[
LCOE_g = \frac{CRF(r, L_g) \cdot I_g + C_g^{\text{O\&M,annual}}}{E_g^{\text{annual}}} \tag{FA-11}
\]

where \(I_g\) is the total capital cost, \(L_g\) is the asset lifetime, and \(E_g^{\text{annual}}\) is the average annual generation.

**Value-Adjusted LCOE (VALCOE):**

\[
VALCOE_g = LCOE_g - \frac{\sum_t \lambda_t \cdot p_{g,t}}{\sum_t p_{g,t}} \tag{FA-12}
\]

where \(\lambda_t\) is the electricity price at hour \(t\). Negative VALCOE indicates that the technology's generation is, on average, sold at prices exceeding its cost — i.e., the technology is profitable at market prices.


### Debt Service Coverage Ratio (DSCR)

\[
DSCR_y = \frac{CFADS_y}{DS_y} \tag{FA-13}
\]

| Symbol | Description |
|--------|-------------|
| \(CFADS_y\) | Cash Flow Available for Debt Service: net operating income before debt payments |
| \(DS_y\) | Debt Service: scheduled principal + interest payment (constant annuity) |

A DSCR above 1.0 means the project generates enough cash to cover its debt obligations. Lenders typically require a minimum DSCR of 1.20–1.40 depending on the technology and market risk.


### Loan Life Coverage Ratio (LLCR)

\[
LLCR = \frac{\sum_{y=0}^{T-1} CFADS_y / (1+r)^y}{D_{\text{outstanding}}} \tag{FA-14}
\]

where \(T\) is the remaining debt tenor and \(D_{\text{outstanding}}\) is the outstanding debt principal. The LLCR measures the present-value ability of the project to repay its debt over the remaining loan life.


### Depreciation

**Straight-line:**

\[
\text{Dep}_y = \frac{CAPEX}{n_{\text{dep}}} \qquad \text{for } y = 0, \ldots, n_{\text{dep}} - 1 \tag{FA-15}
\]

**MACRS (5-year schedule):**

\[
\text{Dep}_y = CAPEX \cdot m_y, \quad m = [0.20, \; 0.32, \; 0.192, \; 0.1152, \; 0.1152, \; 0.0576] \tag{FA-16}
\]


### Debt Service (Constant Annuity)

\[
DS = D_0 \cdot CRF(r_d, T) \tag{FA-17}
\]

where \(D_0 = CAPEX \cdot D\) is the debt principal, \(r_d\) is the cost of debt, and \(T\) is the debt tenor. The annuity payment is constant over the loan life, split between interest and principal repayment.


---


## Scripting

All wizard computations are available as Python functions for batch processing and Jupyter notebooks:

```python
from esfex.models.financial_analysis import (
    FinancialAssumptions,
    compute_system_financials,
    compute_technology_financials,
    run_sensitivity_analysis,
    run_monte_carlo,
)

assumptions = FinancialAssumptions(
    discount_rate=0.08,
    debt_fraction=0.70,
    ppa_price=65.0,
    carbon_price=25.0,
)

sf = compute_system_financials("results.h5", assumptions)
print(f"NPV: ${sf.npv_total:,.0f}, IRR: {sf.project_irr:.1%}")

techs = compute_technology_financials("results.h5", assumptions)
for name, tf in techs.items():
    print(f"  {name}: LCOE=${tf.lcoe:.1f}/MWh, CF={tf.capacity_factor:.1%}")

sens = run_sensitivity_analysis("results.h5", assumptions)
mc = run_monte_carlo("results.h5", assumptions, n_samples=5000)
print(f"P(NPV>0) = {(mc.npv_samples > 0).mean():.0%}")
```

See the [Financial Analysis API](../api/models-financial-analysis.md) for full parameter documentation.
