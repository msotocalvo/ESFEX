# Financial Analysis
Module: `esfex.models.financial_analysis`

## Overview

Post-optimization financial assessment of energy system investments. Reads completed simulation results from HDF5 and computes investment metrics used in power project finance [**[68]**](../reference/bibliography.md#ref68): net present value (NPV) decomposition, internal rate of return (IRR), weighted average cost of capital (WACC), debt service coverage ratio (DSCR), levelized cost of energy (LCOE) [**[48]**](../reference/bibliography.md#ref48) [**[64]**](../reference/bibliography.md#ref64), and value-adjusted LCOE (VALCOE).

The module is GUI-independent and can be used from scripts and notebooks. The [Financial Analysis Wizard](../workflows/financial-analysis.md) provides a graphical interface to the same functions.

**Data flow:**

```
HDF5 results file (from Orchestrator.run())
    │
    ├── /cost_breakdown/year_YYYY/    ──→  granular cost components (preferred)
    │   (27 component attributes)
    │
    ├── /detailed_results/year_YYYY/  ──→  generation, prices, investments
    │   (gen_output, electricity_prices, ...)
    │
    └── /system_configuration/        ──→  fuel costs, invest costs, lifetimes
        (generators/, batteries/)

        ↓
    FinancialAssumptions  +  HDF5 data
        ↓
    SystemFinancials / TechnologyFinancials / SensitivityResult / MonteCarloResult
```

When the `/cost_breakdown/` group is present in HDF5 (see [HDF5 schema](../reference/hdf5-output-schema.md#cost_breakdown)), granular per-component costs are used directly. Otherwise, costs are recalculated from generation output and system configuration (fuel cost × generation, O&M × generation, etc.).

---

## 1. FinancialAssumptions

User-configurable financial parameters. All fields have sensible defaults and can be overridden selectively.

```python
@dataclass
class FinancialAssumptions:
    # Capital structure
    debt_fraction: float = 0.60
    cost_of_debt: float = 0.05
    cost_of_equity: float = 0.12
    debt_tenor: int = 15

    # Tax & depreciation
    tax_rate: float = 0.25
    depreciation_method: str = "straight_line"
    depreciation_years: int = 20
    itc_rate: float = 0.0
    ptc_rate: float = 0.0

    # Revenue
    ppa_price: float = 0.0
    ppa_escalation: float = 0.02
    capacity_payment: float = 0.0
    rec_price: float = 0.0

    # Environmental
    carbon_price: float = 0.0
    carbon_price_escalation: float = 0.02

    # Other
    insurance_rate: float = 0.005
    salvage_fraction: float = 0.05
    discount_rate: float = 0.08
```

### Capital Structure

| Field | Default | Description |
|-------|---------|-------------|
| `debt_fraction` | 0.60 | Proportion of capital financed through debt, \(D \in [0, 1]\). Typical utility-scale projects: 0.60–0.80. |
| `cost_of_debt` | 0.05 | Annual interest rate on debt, \(r_d\). |
| `cost_of_equity` | 0.12 | Required return on equity, \(r_e\). Reflects the risk premium investors require above the risk-free rate. |
| `debt_tenor` | 15 | Loan repayment period in years, \(T\). |

### Tax & Depreciation

| Field | Default | Description |
|-------|---------|-------------|
| `tax_rate` | 0.25 | Corporate tax rate, \(t\). |
| `depreciation_method` | `"straight_line"` | `"straight_line"`: uniform annual deduction over `depreciation_years`. `"macrs"`: Modified Accelerated Cost Recovery using a 5-year schedule (20%, 32%, 19.2%, 11.52%, 11.52%, 5.76%). |
| `depreciation_years` | 20 | Asset depreciation lifetime for tax purposes. |
| `itc_rate` | 0.0 | Investment Tax Credit as fraction of CAPEX. Applied as a lump-sum benefit in year 0. |
| `ptc_rate` | 0.0 | Production Tax Credit in \$/MWh. Applied annually proportional to renewable generation. |

### Revenue

| Field | Default | Description |
|-------|---------|-------------|
| `ppa_price` | 0.0 | Power Purchase Agreement price (\$/MWh). When 0, nodal prices from the optimization (shadow prices of the power balance constraint) are used as the revenue basis. |
| `ppa_escalation` | 0.02 | Annual PPA price escalation rate. Price in year \(y\): \(P_0 (1 + e)^y\). |
| `capacity_payment` | 0.0 | Annual capacity payment (\$/MW-year). Multiplied by total installed capacity. |
| `rec_price` | 0.0 | Renewable Energy Certificate price (\$/MWh). Applied to renewable generation only. |

### Environmental

| Field | Default | Description |
|-------|---------|-------------|
| `carbon_price` | 0.0 | Carbon price (\$/tCO2). Revenue is computed from avoided emissions relative to the first year. |
| `carbon_price_escalation` | 0.02 | Annual carbon price escalation rate. |

### Other

| Field | Default | Description |
|-------|---------|-------------|
| `insurance_rate` | 0.005 | Annual insurance cost as fraction of total CAPEX. |
| `salvage_fraction` | 0.05 | Residual asset value at end of life, as fraction of CAPEX. Applied in the final year. |
| `discount_rate` | 0.08 | Discount rate for NPV calculations, \(r\). |

---

## 2. compute_system_financials

```python
def compute_system_financials(
    h5_path: Path | str,
    assumptions: FinancialAssumptions | None = None,
) -> SystemFinancials
```

Compute system-level financial analysis from simulation results.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `h5_path` | `Path` or `str` | required | Path to the HDF5 results file. |
| `assumptions` | `FinancialAssumptions` or `None` | `None` | Financial parameters. Uses defaults if `None`. |

**Returns:** `SystemFinancials` containing NPV decomposition, annual cash flow table, return metrics, debt metrics, and system LCOE.

**Computation pipeline:**

1. Load system configuration (generators, batteries, technologies) and year list from HDF5.
2. For each year, load generation output, investment decisions, and electricity prices.
3. Compute annual costs: if `/cost_breakdown/year_YYYY/` exists, read directly; otherwise recalculate from generation × unit cost parameters.
4. Compute annual revenue from energy sales (PPA or nodal prices), carbon credits, RECs, and capacity payments.
5. Compute WACC, depreciation schedule, debt service, and tax.
6. Build the annual cash flow table and discount all flows to NPV.
7. Compute IRR, MIRR, payback, DSCR, LLCR, and system LCOE.

**Example:**

```python
from esfex.models.financial_analysis import (
    FinancialAssumptions,
    compute_system_financials,
)

assumptions = FinancialAssumptions(
    discount_rate=0.08,
    debt_fraction=0.70,
    cost_of_debt=0.04,
    tax_rate=0.20,
    ppa_price=65.0,
    carbon_price=25.0,
)

sf = compute_system_financials("results/results_isla.h5", assumptions)

print(f"NPV:         ${sf.npv_total:,.0f}")
print(f"Project IRR: {sf.project_irr:.1%}")
print(f"WACC:        {sf.wacc:.1%}")
print(f"DSCR (min):  {sf.dscr_min:.2f}")
print(f"System LCOE: ${sf.lcoe_system:.1f}/MWh")
print(f"Payback:     {sf.payback_discounted:.1f} years")

# Annual cash flow table (pandas DataFrame)
print(sf.cash_flows[["year", "revenue", "net_cash_flow", "cumulative_npv"]])
```

---

## 3. SystemFinancials

Complete system-level financial results.

### NPV Decomposition

Each component is discounted using Eq. (FA-1):

\[
NPV_k = \sum_{y=0}^{N-1} \frac{C_{k,y}}{(1 + r)^y}
\]

| Field | Type | Description |
|-------|------|-------------|
| `npv_capex` | `float` | NPV of capital expenditures (\$). |
| `npv_fuel` | `float` | NPV of fuel costs (\$). |
| `npv_om` | `float` | NPV of O&M + startup + insurance costs (\$). |
| `npv_decommissioning` | `float` | NPV of decommissioning costs (\$). |
| `npv_penalties` | `float` | NPV of penalty costs: load shedding, curtailment, RE shortfall (\$). |
| `npv_revenue` | `float` | NPV of energy revenue (\$). |
| `npv_tax_benefits` | `float` | NPV of tax depreciation benefits + ITC + PTC (\$). |
| `npv_salvage` | `float` | NPV of residual asset value (\$). |
| `npv_total` | `float` | Net project NPV: revenue + tax benefits + salvage − all costs (\$). |

### Cash Flows

| Field | Type | Description |
|-------|------|-------------|
| `cash_flows` | `pd.DataFrame` | Annual pro-forma cash flow table. Columns: `year`, `revenue`, `fuel_cost`, `om_cost`, `insurance`, `capex`, `depreciation`, `tax`, `ptc_benefit`, `debt_service`, `net_cash_flow`, `equity_cash_flow`, `cumulative_npv`, `dscr`. |

### Return Metrics

| Field | Type | Description |
|-------|------|-------------|
| `project_irr` | `float` | Project IRR. Discount rate at which NPV = 0, using pre-debt cash flows. |
| `equity_irr` | `float` | Equity IRR. Uses post-debt-service cash flows. |
| `mirr` | `float` | Modified IRR [**[69]**](../reference/bibliography.md#ref69). Finance rate = cost of debt; reinvestment rate = cost of equity. |
| `payback_simple` | `float` | Simple payback period (years). |
| `payback_discounted` | `float` | Discounted payback period (years). |
| `wacc` | `float` | Weighted Average Cost of Capital. |
| `profitability_index` | `float` | NPV of revenue / NPV of CAPEX. |

### Debt Metrics

| Field | Type | Description |
|-------|------|-------------|
| `dscr_annual` | `np.ndarray` | Annual DSCR: \(CFADS_y / DS_y\). |
| `dscr_min` | `float` | Minimum annual DSCR during the debt tenor. |
| `llcr` | `float` | Loan Life Coverage Ratio: NPV of CFADS over loan tenor / outstanding debt. |
| `cfads` | `np.ndarray` | Annual Cash Flow Available for Debt Service (\$). |

### System Cost Metrics

| Field | Type | Description |
|-------|------|-------------|
| `lcoe_system` | `float` | System LCOE (\$/MWh): NPV(costs) / NPV(generation) [**[48]**](../reference/bibliography.md#ref48) [**[64]**](../reference/bibliography.md#ref64). |
| `lcoe_by_tech` | `dict` | Per-technology LCOE: `{name: float}`. |
| `lcos_by_battery` | `dict` | Per-battery levelized cost of storage: `{name: float}`. |
| `valcoe_by_tech` | `dict` | Per-technology VALCOE: `{name: float}`. |
| `tech_financials` | `dict` | Per-technology detailed financials: `{name: TechnologyFinancials}`. |

---

## 4. compute_technology_financials

```python
def compute_technology_financials(
    h5_path: Path | str,
    assumptions: FinancialAssumptions | None = None,
) -> dict[str, TechnologyFinancials]
```

Compute per-technology financial breakdown. Returns one `TechnologyFinancials` entry per generator and battery, including virtual units from technology investments.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `h5_path` | `Path` or `str` | required | Path to the HDF5 results file. |
| `assumptions` | `FinancialAssumptions` or `None` | `None` | Financial parameters. |

**Returns:** `dict` mapping technology name to `TechnologyFinancials`.

**Per-technology LCOE computation:**

\[
LCOE_g = \frac{CRF(r, L_g) \cdot I_g + C_g^{\text{O\&M,annual}} + C_g^{\text{fuel,annual}}}{E_g^{\text{annual}}}
\]

where \(I_g\) is total capital cost, \(L_g\) is asset lifetime, \(CRF\) is the capital recovery factor (Eq. FA-6 in [Wizard formulations](../workflows/financial-analysis.md#mathematical-formulations)), and \(E_g^{\text{annual}}\) is average annual generation.

**Example:**

```python
techs = compute_technology_financials("results/results_isla.h5")

for name, tf in techs.items():
    print(f"{name}:")
    print(f"  Installed:  {tf.installed_mw:.0f} MW")
    print(f"  CF:         {tf.capacity_factor:.1%}")
    print(f"  LCOE:       ${tf.lcoe:.1f}/MWh")
    print(f"  VALCOE:     ${tf.valcoe:.1f}/MWh")
    print(f"  Revenue:    ${tf.revenue_total:,.0f}")
    print(f"  ROI:        {tf.roi:.1%}")
```

---

## 5. TechnologyFinancials

Per-technology financial breakdown.

### Investment and Capacity

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Technology name (e.g., `"Solar_PV"`, `"Investment Battery"`). |
| `tech_type` | `str` | `"generator"` or `"battery"`. |
| `fuel_type` | `str` | Fuel type (e.g., `"Sun"`, `"Diesel"`, `"Li-ion"`). |
| `capex_total` | `float` | Total capital expenditure (\$). |
| `installed_mw` | `float` | Installed capacity (MW). |

### Production

| Field | Type | Description |
|-------|------|-------------|
| `generation_mwh` | `float` | Total generation over all years (MWh). |
| `annual_generation` | `np.ndarray` | Annual generation (MWh), one element per year. |
| `capacity_factor` | `float` | Average capacity factor: \(\overline{CF} = E_{\text{total}} / (P_{\text{rated}} \cdot H \cdot N)\). |

### Revenue and Costs

| Field | Type | Description |
|-------|------|-------------|
| `revenue_total` | `float` | Total revenue (\$). |
| `annual_revenue` | `np.ndarray` | Annual revenue (\$). |
| `average_selling_price` | `float` | Revenue-weighted average selling price (\$/MWh): \(\sum \lambda_t p_t / \sum p_t\). |
| `fuel_cost_total` | `float` | Total fuel cost (\$). |
| `om_cost_total` | `float` | Total O&M cost (\$). |
| `startup_cost_total` | `float` | Total startup cost (\$). |
| `co2_cost_total` | `float` | Total CO2 emission cost (\$). |

### Performance Metrics

| Field | Type | Description |
|-------|------|-------------|
| `lcoe` | `float` | Levelized cost of energy (\$/MWh). |
| `valcoe` | `float` | Value-adjusted LCOE (\$/MWh). Negative = profitable at market prices. |
| `roi` | `float` | Return on investment: \((R - C) / CAPEX\). |
| `contribution_to_npv` | `float` | Technology's contribution to system NPV (\$). |

### Storage-Specific (batteries only; `NaN` for generators)

| Field | Type | Description |
|-------|------|-------------|
| `lcos` | `float` | Levelized cost of storage (\$/MWh discharged). |
| `arbitrage_revenue` | `float` | Arbitrage revenue from price spreads (\$). |
| `degradation_cost` | `float` | Battery degradation cost (\$). |

---

## 6. run_sensitivity_analysis

```python
def run_sensitivity_analysis(
    h5_path: Path | str,
    assumptions: FinancialAssumptions,
    variables: list[str] | None = None,
    range_pct: float = 0.30,
    n_points: int = 11,
) -> SensitivityResult
```

One-at-a-time (OAT) sensitivity analysis [**[12]**](../reference/bibliography.md#ref12). Sweeps each selected financial assumption independently over a range around its base value and records the impact on NPV and IRR. Produces data for tornado diagrams and spider plots.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `h5_path` | `Path` or `str` | required | HDF5 results file. |
| `assumptions` | `FinancialAssumptions` | required | Base-case assumptions. |
| `variables` | `list[str]` or `None` | `None` | Assumption field names to sweep. Defaults to `["discount_rate", "ppa_price", "carbon_price", "debt_fraction", "cost_of_debt", "tax_rate"]`. |
| `range_pct` | `float` | 0.30 | Variation range: ±30% of base value. For base=0, sweeps from \(-\Delta\) to \(+\Delta\). |
| `n_points` | `int` | 11 | Evaluation points per variable (linearly spaced). |

**Returns:** `SensitivityResult`.

For each variable \(x_k\), the sweep evaluates NPV and IRR at \(n\) points linearly spaced from \(x_k(1-\Delta)\) to \(x_k(1+\Delta)\):

\[
NPV_k(x) = \text{compute\_system\_financials}(h5, \; \text{replace}(\theta, x_k = x)).npv\_total
\]

**Example:**

```python
from esfex.models.financial_analysis import (
    FinancialAssumptions,
    run_sensitivity_analysis,
)

assumptions = FinancialAssumptions(discount_rate=0.08, ppa_price=65.0)
result = run_sensitivity_analysis(
    "results.h5",
    assumptions,
    variables=["discount_rate", "ppa_price", "carbon_price", "tax_rate"],
    range_pct=0.40,
)

# Tornado: rank by NPV spread
for var, (npv_lo, npv_hi) in sorted(
    result.tornado.items(),
    key=lambda x: abs(x[1][1] - x[1][0]),
    reverse=True,
):
    print(f"  {var}: NPV range ${npv_lo:,.0f} to ${npv_hi:,.0f}")

# Break-even: discount rate where NPV = 0
if "discount_rate" in result.breakeven:
    print(f"  Break-even discount rate: {result.breakeven['discount_rate']:.2%}")
```

### SensitivityResult

| Field | Type | Description |
|-------|------|-------------|
| `base_npv` | `float` | NPV at base-case assumptions (\$). |
| `base_irr` | `float` | IRR at base-case assumptions. |
| `sweeps` | `dict` | `{variable: [(value, npv, irr), ...]}`. Full sweep data. |
| `tornado` | `dict` | `{variable: (npv_low, npv_high)}`. NPV at the extremes of each sweep. |
| `breakeven` | `dict` | `{variable: value}`. Parameter value where NPV crosses zero (linear interpolation). Only present when a sign change occurs within the sweep range. |

---

## 7. run_monte_carlo

```python
def run_monte_carlo(
    h5_path: Path | str,
    assumptions: FinancialAssumptions,
    distributions: dict[str, tuple[str, float, float]] | None = None,
    n_samples: int = 1000,
    seed: int | None = 42,
) -> MonteCarloResult
```

Monte Carlo simulation of financial outcomes [**[70]**](../reference/bibliography.md#ref70). Samples financial assumptions from probability distributions, computes NPV and IRR for each sample, and reports summary statistics including Value-at-Risk (VaR) and Conditional VaR (CVaR).

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `h5_path` | `Path` or `str` | required | HDF5 results file. |
| `assumptions` | `FinancialAssumptions` | required | Base-case assumptions. |
| `distributions` | `dict` or `None` | `None` | Variable distributions. Format: `{field_name: (dist_type, param1, param2)}`. See table below. Defaults to normal distributions on discount rate and PPA price, uniform on tax rate. |
| `n_samples` | `int` | 1000 | Monte Carlo iterations. |
| `seed` | `int` or `None` | 42 | Random seed. `None` for non-deterministic. |

**Supported distributions:**

| Type | Parameters | Sampling |
|------|-----------|----------|
| `"normal"` | \(\mu\), \(\sigma\) | \(X \sim \mathcal{N}(\mu, \sigma^2)\) |
| `"uniform"` | \(a\), \(b\) | \(X \sim \mathcal{U}(a, b)\) |
| `"triangular"` | \(a\), \(b\) | \(X \sim \text{Tri}(a, \; x_0, \; b)\) where \(x_0\) is the base-case value (mode) |

**Risk metrics:**

\[
VaR_\alpha = F^{-1}_{NPV}(\alpha), \qquad CVaR_\alpha = \mathbb{E}[NPV \mid NPV \leq VaR_\alpha]
\]

where \(\alpha = 0.05\). VaR is the NPV value exceeded with 95% probability; CVaR is the expected NPV in the worst 5% of outcomes.

**Example:**

```python
from esfex.models.financial_analysis import (
    FinancialAssumptions,
    run_monte_carlo,
)

assumptions = FinancialAssumptions(discount_rate=0.08, ppa_price=65.0)
mc = run_monte_carlo(
    "results.h5",
    assumptions,
    distributions={
        "discount_rate": ("normal", 0.08, 0.01),
        "ppa_price": ("normal", 65.0, 8.0),
        "carbon_price": ("uniform", 10.0, 50.0),
        "tax_rate": ("triangular", 0.15, 0.35),
    },
    n_samples=5000,
)

print(f"NPV mean:    ${mc.npv_mean:,.0f}")
print(f"NPV median:  ${mc.npv_p50:,.0f}")
print(f"NPV std:     ${mc.npv_std:,.0f}")
print(f"VaR (5%):    ${mc.npv_var_5:,.0f}")
print(f"CVaR (5%):   ${mc.npv_cvar_5:,.0f}")
print(f"IRR mean:    {mc.irr_mean:.1%}")
print(f"P(NPV > 0):  {(mc.npv_samples > 0).mean():.0%}")
```

### MonteCarloResult

| Field | Type | Description |
|-------|------|-------------|
| `n_samples` | `int` | Number of iterations. |
| `npv_samples` | `np.ndarray` | All NPV samples. Shape: `(n_samples,)`. |
| `irr_samples` | `np.ndarray` | All IRR samples. Shape: `(n_samples,)`. |
| `npv_mean` | `float` | Mean NPV (\$). |
| `npv_std` | `float` | Standard deviation of NPV (\$). |
| `npv_p5` | `float` | 5th percentile NPV. |
| `npv_p25` | `float` | 25th percentile NPV. |
| `npv_p50` | `float` | Median NPV. |
| `npv_p75` | `float` | 75th percentile NPV. |
| `npv_p95` | `float` | 95th percentile NPV. |
| `npv_var_5` | `float` | Value-at-Risk at 5%: NPV exceeded with 95% probability. |
| `npv_cvar_5` | `float` | Conditional VaR at 5%: mean NPV of the worst 5% outcomes. |
| `irr_mean` | `float` | Mean IRR. |
| `irr_std` | `float` | Standard deviation of IRR. |

---

## 8. Full Example

```python
from esfex.models.financial_analysis import (
    FinancialAssumptions,
    compute_system_financials,
    compute_technology_financials,
    run_sensitivity_analysis,
    run_monte_carlo,
)

# 1. Configure assumptions for a specific market
assumptions = FinancialAssumptions(
    discount_rate=0.08,
    debt_fraction=0.70,
    cost_of_debt=0.045,
    cost_of_equity=0.14,
    debt_tenor=18,
    tax_rate=0.22,
    depreciation_method="macrs",
    ppa_price=62.0,
    ppa_escalation=0.015,
    carbon_price=30.0,
    carbon_price_escalation=0.03,
    itc_rate=0.30,
)

h5 = "results/results_isla_juventud.h5"

# 2. System-level analysis
sf = compute_system_financials(h5, assumptions)
print(f"NPV:          ${sf.npv_total:,.0f}")
print(f"Project IRR:  {sf.project_irr:.1%}")
print(f"Equity IRR:   {sf.equity_irr:.1%}")
print(f"WACC:         {sf.wacc:.1%}")
print(f"System LCOE:  ${sf.lcoe_system:.1f}/MWh")
print(f"Min DSCR:     {sf.dscr_min:.2f}")
print(f"Payback:      {sf.payback_discounted:.1f} years")

# Cash flow table
print(sf.cash_flows[["year", "revenue", "fuel_cost", "net_cash_flow", "dscr"]])

# 3. Per-technology breakdown
techs = compute_technology_financials(h5, assumptions)
for name, tf in techs.items():
    print(f"  {name}: LCOE=${tf.lcoe:.1f}/MWh, CF={tf.capacity_factor:.1%}, "
          f"ROI={tf.roi:.0%}")

# 4. Sensitivity — which assumptions matter most?
sens = run_sensitivity_analysis(h5, assumptions)
for var, (lo, hi) in sorted(
    sens.tornado.items(),
    key=lambda x: abs(x[1][1] - x[1][0]),
    reverse=True,
):
    print(f"  {var}: NPV range ${lo:,.0f} to ${hi:,.0f}")

# 5. Monte Carlo — what is the probability of success?
mc = run_monte_carlo(h5, assumptions, n_samples=5000)
print(f"NPV P50:     ${mc.npv_p50:,.0f}")
print(f"VaR (5%):    ${mc.npv_var_5:,.0f}")
print(f"P(NPV > 0):  {(mc.npv_samples > 0).mean():.0%}")
```
