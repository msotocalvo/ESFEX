# Sensitivity Analysis

Module: `esfex.sensitivity`

Submodules: `esfex.sensitivity.engine`, `esfex.sensitivity.lp_parser`, `esfex.sensitivity.worker`

Requires: `pip install "esfex[sensitivity]"` (installs SALib, scipy)

## Overview

Sobol global sensitivity analysis [**[11]**](../reference/bibliography.md#ref11), [**[12]**](../reference/bibliography.md#ref12) (via SALib [**[19]**](../reference/bibliography.md#ref19)) varies all parameters simultaneously and decomposes output variance into contributions from each parameter and their interactions.

**Two analysis modes:**

| Mode | Speed | Fidelity | Use Case |
|------|-------|----------|----------|
| LP-level (`"lp"`) | Fast (seconds per eval) | Approximate (linear model) | Screening, quick parameter importance ranking |
| Config-level (`"config"`) | Slow (minutes per eval) | Exact (full nonlinear model) | Final analysis, publication-quality results |

---

## SensitivityEngine

Orchestrates Sobol sensitivity analysis using SALib.

```python
class SensitivityEngine:
    def __init__(
        self,
        mode: str,
        parameters: list[SensitivityParameter],
        kpi_names: list[str] | None = None,
        n_base_samples: int = 128,
    )
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mode` | `str` | required | Analysis mode: `"lp"` for LP-level or `"config"` for config-level. |
| `parameters` | `list[SensitivityParameter]` | required | List of parameters to vary. |
| `kpi_names` | `list[str]` or `None` | `None` | KPI names to evaluate. If `None`, uses the default set: `["total_cost", "inv_gen_total", "inv_bat_total", "curtailment", "load_shedding"]`. |
| `n_base_samples` | `int` | `128` | Sobol N parameter. Total evaluations = `N * (2D + 2)` where D = number of parameters. |

**Properties:**

| Property | Type | Description |
|----------|------|-------------|
| `problem` | `dict` | SALib problem definition with `num_vars`, `names`, and `bounds`. |
| `n_evaluations` | `int` | Total number of model evaluations required: `N * (2D + 2)`. |

### generate_samples

```python
def generate_samples(self) -> np.ndarray
```

Generate Saltelli sample matrix using `SALib.sample.saltelli`.

**Returns:** Array of shape `(N*(2D+2), D)` where each row is a parameter combination.

### run_lp_analysis

```python
def run_lp_analysis(
    self,
    lp_path: str,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> SobolResult
```

Run LP-level Sobol analysis. Parses the LP file once, then perturbs objective coefficients and/or RHS values for each sample, re-solving with scipy's HiGHS backend.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `lp_path` | `str` | Path to a `.lp` file exported from JuMP (e.g., via `MasterProblemAdapter.write_lp()`). |
| `progress_callback` | callable or `None` | Optional callback `(current, total, message)` for progress tracking. |

**Returns:** `SobolResult` with first-order (S1) and total-effect (ST) indices.

**How it works:**

1. Parse the LP file into an `LPModel` (variables, constraints, objective).
2. Auto-detect parameter groups from variable naming patterns (e.g., `gen_inv_y*_g1_*` -> `"inv_gen_1"`).
3. For each Saltelli sample, multiply the relevant objective coefficients or RHS values by the sample multiplier.
4. Re-solve the perturbed LP using `scipy.optimize.linprog` with HiGHS.
5. Extract KPIs from the solution.
6. Compute Sobol indices using `SALib.analyze.sobol`.

### run_config_analysis

```python
def run_config_analysis(
    self,
    base_config_path: str,
    output_dir: str,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> SobolResult
```

Run config-level Sobol analysis. Creates modified YAML configs and runs full ESFEX simulations per sample.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `base_config_path` | `str` | Path to the base YAML configuration file. |
| `output_dir` | `str` | Output directory for intermediate results and temp configs. |
| `progress_callback` | callable or `None` | Optional progress callback. |

**Returns:** `SobolResult` with Sobol indices.

**How it works:**

1. Load the base YAML config.
2. For each Saltelli sample, apply parameter multipliers to create a modified config (investment costs, fuel costs, demand growth, etc.).
3. Write the modified config to a temporary YAML file.
4. Run a full ESFEX simulation via subprocess: `python -m esfex.cli run --config temp.yaml --output run_dir`.
5. Extract KPIs from the HDF5 results.
6. Compute Sobol indices.

**Config-level parameter application:**

`_apply_config_multipliers()` applies multipliers to each system's configuration:
- `invest_cost_renewables`: Multiplies `invest_cost` for solar/wind/PV generators.
- `invest_cost_conventional`: Multiplies `invest_cost` for non-renewable generators.
- `invest_cost_storage`: Multiplies `invest_cost_power` and `invest_cost_capacity` for batteries.
- `invest_cost_transmission`: Multiplies transmission investment costs.
- `fuel_cost`: Multiplies `fuel_cost` for all generators.
- `maintenance_cost`: Multiplies maintenance costs.
- `demand_growth`: Multiplies the `growth_rate` in demand configuration.
- `fuel_price_growth`: Multiplies fuel price escalation rates.
- `carbon_price`: Multiplies carbon pricing parameters.

---

## SensitivityParameter

Defines a single parameter to vary in the analysis.

```python
@dataclass
class SensitivityParameter:
    name: str               # Display name
    key: str                # Internal key for matching
    lower_bound: float      # Lower multiplier bound (default 0.5)
    upper_bound: float      # Upper multiplier bound (default 2.0)
    category: str           # "objective", "rhs", or "config"
```

**Categories:**

| Category | Used In | Description |
|----------|---------|-------------|
| `"objective"` | LP mode | Multiplier applied to objective coefficients in the LP model. |
| `"rhs"` | LP mode | Multiplier applied to constraint right-hand-side values. |
| `"config"` | Config mode | Multiplier applied to YAML config parameters. |

**Example:**

```python
from esfex.sensitivity.engine import SensitivityParameter

params = [
    SensitivityParameter(
        name="Solar Investment Cost",
        key="invest_cost_renewables",
        lower_bound=0.5,
        upper_bound=2.0,
        category="config",
    ),
    SensitivityParameter(
        name="Diesel Fuel Cost",
        key="fuel_cost",
        lower_bound=0.5,
        upper_bound=3.0,
        category="config",
    ),
]
```

---

## SobolResult

Results from a Sobol analysis run.

```python
@dataclass
class SobolResult:
    parameters: list[str]                           # Parameter names
    kpi_names: list[str]                            # KPI names
    S1: dict[str, np.ndarray]                       # First-order indices {kpi: array}
    ST: dict[str, np.ndarray]                       # Total-effect indices {kpi: array}
    S1_conf: dict[str, np.ndarray]                  # S1 confidence intervals
    ST_conf: dict[str, np.ndarray]                  # ST confidence intervals
    n_samples: int                                  # Base Sobol N
    n_evaluations: int                              # Total evaluations performed
```

### Sobol Index Interpretation

**First-Order Index (S1):**

The fraction of output variance explained by varying this parameter alone:

| S1 Value | Interpretation |
|----------|----------------|
| 0.0 - 0.05 | Negligible direct effect |
| 0.05 - 0.20 | Minor influence |
| 0.20 - 0.50 | Significant influence |
| 0.50 - 1.00 | Dominant parameter |

**Total-Order Index (ST):**

The fraction of output variance explained by this parameter including all interactions:

- **ST ~ S1**: The parameter acts independently (no interactions).
- **ST >> S1**: Significant interaction effects with other parameters.
- **Sum of all ST > 1**: Indicates strong parameter interactions.

### to_csv

```python
def to_csv(self, filepath: str | Path) -> None
```

Export Sobol indices to a CSV file with columns: `KPI, Parameter, S1, S1_conf, ST, ST_conf`.

---

## Available KPIs

| KPI Name | Description | Units |
|----------|-------------|-------|
| `total_cost` | NPV of total system cost (investment + operational + penalties) | $ |
| `inv_gen_total` | Total generation investment (MW capacity installed) | MW |
| `inv_bat_total` | Total storage investment (MW power capacity) | MW |
| `curtailment` | Total RE curtailment | MWh |
| `load_shedding` | Total unserved energy | MWh |

---

## Predefined Config Parameters

```python
from esfex.sensitivity.engine import get_config_parameters

params = get_config_parameters()
```

Returns `SensitivityParameter` objects for:

| Key | Display Name | Bounds |
|-----|-------------|--------|
| `invest_cost_renewables` | RE Investment Cost | 0.5 - 2.0 |
| `invest_cost_storage` | Storage Investment Cost | 0.5 - 2.0 |
| `invest_cost_conventional` | Conv. Investment Cost | 0.5 - 2.0 |
| `invest_cost_transmission` | Transmission Inv. Cost | 0.5 - 2.0 |
| `fuel_cost` | Fuel Cost | 0.5 - 3.0 |
| `maintenance_cost` | Maintenance Cost | 0.5 - 2.0 |
| `demand_growth` | Demand Growth | 0.8 - 1.5 |
| `fuel_price_growth` | Fuel Price Growth | 0.5 - 2.0 |
| `carbon_price` | Carbon Price | 0.0 - 3.0 |

---

## LP-Level Auto-Detection

Parameters can be auto-detected from the LP file:

```python
from esfex.sensitivity.engine import get_lp_parameters

params = get_lp_parameters("master_problem.lp")
```

Returns `SensitivityParameter` objects for each detected group:

**Objective coefficient groups (from variable name patterns):**

| Pattern | Group Name | Description |
|---------|-----------|-------------|
| `gen_inv_y*_gN_*` | `inv_gen_N` | Generator N investment cost |
| `bat_pow_inv_y*_biN_*` | `inv_bat_pow_N` | Battery N power investment |
| `bat_cap_inv_y*_biN_*` | `inv_bat_cap_N` | Battery N capacity investment |
| `op_gen_y*_d*_N,*` | `op_gen_N` | Operational cost generator N |
| `op_ll_*` | `op_load_shedding` | Load shedding penalty |
| `op_fre_loss_*` | `op_fre_penalty` | Frequency penalty |

**RHS groups (from constraint name patterns):**

| Pattern | Group Name | Description |
|---------|-----------|-------------|
| `demand_bal_*`, `power_balance_*` | `demand` | Demand balance RHS |
| `re_target_*`, `re_ratio_*` | `re_target` | RE target RHS |
| `co2_*`, `carbon_*` | `co2_budget` | CO2 budget RHS |
| `budget_*` | `cost_budget` | Budget constraint RHS |

---

## LPModel (LP Parser)

Parses CPLEX LP format files (exported by JuMP) into scipy-compatible structures for fast re-solving.

```python
from esfex.sensitivity.lp_parser import parse_lp_file, solve_lp, perturb_and_solve

model = parse_lp_file("master_problem.lp")
print(f"Variables: {model.n_vars}, Constraints: {model.n_constraints}")

# Solve baseline
obj_val, solution = solve_lp(model)

# Perturb and re-solve
kpis = perturb_and_solve(
    model,
    obj_multipliers={"inv_gen_0": 1.5, "op_gen_0": 0.8},
    rhs_multipliers={"demand": 1.1},
)
```

Reads CPLEX LP format sections (minimize/maximize, subject to, bounds, end) and builds sparse scipy matrices for efficient re-solving. Each perturbation creates modified coefficient/RHS arrays without re-parsing.

---

## SensitivityWorker (GUI Integration)

Background QThread worker for running sensitivity analysis without blocking the GUI.

```python
from esfex.sensitivity.worker import SensitivityWorker

worker = SensitivityWorker(engine, lp_path="model.lp")
worker.progressChanged.connect(on_progress)  # (current, total, message)
worker.resultReady.connect(on_result)         # SobolResult
worker.errorOccurred.connect(on_error)        # error message
worker.start()

# To cancel:
worker.cancel()
```

**Signals:**

| Signal | Arguments | Description |
|--------|-----------|-------------|
| `progressChanged` | `(int, int, str)` | Current eval, total evals, status message. |
| `resultReady` | `(SobolResult,)` | Analysis complete with result. |
| `errorOccurred` | `(str,)` | Error or cancellation message. |

---

## Computational Cost

Total model evaluations follow the Sobol sampling formula:

```
Total evaluations = N * (2D + 2)
```

where N = `n_base_samples` and D = number of parameters.

| Parameters (D) | Samples (N) | Evaluations | Approx. Time (LP mode) | Approx. Time (Config mode, 4 workers) |
|----------------|-------------|-------------|------------------------|---------------------------------------|
| 3 | 128 | 1,024 | ~1 minute | ~2 hours |
| 5 | 256 | 3,072 | ~5 minutes | ~8 hours |
| 9 | 128 | 2,560 | ~4 minutes | ~6 hours |
| 9 | 256 | 5,120 | ~8 minutes | ~13 hours |

---

## Best Practices

1. **Start with LP mode**: Use LP-level analysis for initial screening, then switch to config-level for important parameters.
2. **Start with few parameters**: Begin with 3-5 most uncertain parameters.
3. **Use reasonable bounds**: Bounds should reflect plausible uncertainty ranges (e.g., 0.5x to 2x for costs).
4. **Sufficient samples**: Use at least N=128 for reliable first-order indices, N=512+ for convergence.
5. **Check convergence**: Run with increasing N and verify indices stabilize.
6. **Prioritize results**: Parameters with high ST values should be modeled carefully or treated stochastically.

---

## Full Example

```python
from esfex.sensitivity.engine import (
    SensitivityEngine,
    get_config_parameters,
)

# Use predefined config-level parameters
params = get_config_parameters()

# Create engine
engine = SensitivityEngine(
    mode="config",
    parameters=params,
    kpi_names=["total_cost", "curtailment", "load_shedding"],
    n_base_samples=64,
)

print(f"Total evaluations needed: {engine.n_evaluations}")

# Run analysis
result = engine.run_config_analysis(
    base_config_path="configs/isla_juventud.yaml",
    output_dir="results/sensitivity/",
    progress_callback=lambda cur, tot, msg: print(f"[{cur}/{tot}] {msg}"),
)

# Export results
result.to_csv("results/sobol_indices.csv")

# Print most influential parameters for total cost
for i, param in enumerate(result.parameters):
    print(f"  {param}: S1={result.S1['total_cost'][i]:.3f}, "
          f"ST={result.ST['total_cost'][i]:.3f}")
```
