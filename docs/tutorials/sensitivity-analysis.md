# Sensitivity Analysis

## Sobol Global Sensitivity Analysis

Energy system models depend on dozens of uncertain parameters. Sobol global sensitivity analysis [**[11]**](../reference/bibliography.md#ref11), [**[12]**](../reference/bibliography.md#ref12) varies all parameters simultaneously and decomposes output variance into contributions from each parameter, capturing both:

- **First-order effects (S1)**: The direct influence of a single parameter, independent of others
- **Total-order effects (ST)**: The combined influence of a parameter, including its interactions with all other parameters

If S_T is much larger than S_1 for a parameter, that parameter's influence depends strongly on other parameters (interaction effects).

---

## Prerequisites

A standard ESFEX install already includes everything needed:

```bash
pip install esfex
```

This bundles SALib [**[19]**](../reference/bibliography.md#ref19) (Sensitivity Analysis Library) and its dependencies as core requirements.

---

## Two Analysis Modes

| Mode | Speed | Accuracy | Use Case |
|------|-------|----------|----------|
| **LP-level** | Fast (seconds per evaluation) | Approximate (perturbs solved LP coefficients) | Quick screening of cost and RHS parameters |
| **Config-level** | Slow (minutes per evaluation) | Exact (re-runs full optimization) | Comprehensive analysis including investment decisions |

### LP-Level Analysis

Parses an exported LP file (`.lp` format), perturbs objective coefficients and constraint right-hand-sides, and re-solves each perturbation. Runs thousands of evaluations in minutes.

### Config-Level Analysis

Creates modified YAML files by applying multipliers to base-case parameters, then runs the full ESFEX optimization for each sample. Captures how parameter changes affect investment decisions, not just operational costs. Each evaluation takes minutes to hours.

---

## Configuration: Defining Parameters

### Predefined Config-Level Parameters

| Parameter Key | Display Name | Default Range | Description |
|--------------|-------------|---------------|-------------|
| `invest_cost_renewables` | RE Investment Cost | [0.5, 2.0] | Scales solar/wind investment costs |
| `invest_cost_storage` | Storage Investment Cost | [0.5, 2.0] | Scales battery investment costs |
| `invest_cost_conventional` | Conv. Investment Cost | [0.5, 2.0] | Scales diesel/gas investment costs |
| `invest_cost_transmission` | Transmission Inv. Cost | [0.5, 2.0] | Scales transmission investment costs |
| `fuel_cost` | Fuel Cost | [0.5, 3.0] | Scales all fuel costs |
| `maintenance_cost` | Maintenance Cost | [0.5, 2.0] | Scales all maintenance costs |
| `demand_growth` | Demand Growth | [0.8, 1.5] | Scales demand growth rate |
| `fuel_price_growth` | Fuel Price Growth | [0.5, 2.0] | Scales fuel price escalation |
| `carbon_price` | Carbon Price | [0.0, 3.0] | Scales CO2 penalty cost |

Ranges are multipliers on base-case values. [0.5, 2.0] means half to double the base-case value.

### Custom Parameters

You can define custom parameters targeting specific configuration paths:

```python
from esfex.sensitivity.engine import SensitivityParameter

custom_params = [
    SensitivityParameter(
        name="Diesel Fuel Cost",
        key="fuel_cost_diesel",
        lower_bound=0.5,
        upper_bound=3.0,
        category="config",
    ),
    SensitivityParameter(
        name="Solar Investment",
        key="invest_cost_renewables",
        lower_bound=0.4,
        upper_bound=1.5,
        category="config",
    ),
    SensitivityParameter(
        name="Battery Investment",
        key="invest_cost_storage",
        lower_bound=0.3,
        upper_bound=2.0,
        category="config",
    ),
    SensitivityParameter(
        name="Demand Growth",
        key="demand_growth",
        lower_bound=0.5,
        upper_bound=2.0,
        category="config",
    ),
    SensitivityParameter(
        name="Discount Rate",
        key="discount_rate",
        lower_bound=0.5,
        upper_bound=2.0,
        category="config",
    ),
]
```

---

## Running from the GUI

1. Complete a simulation run (click **Run** and wait for completion).
2. Click the **Sensitivity** button in the toolbar. The Sensitivity Analysis Dialog opens.
3. In the dialog:
   - **Mode**: Choose "LP-level" (fast) or "Config-level" (accurate)
   - **LP File**: For LP-level, browse to the exported `.lp` file from your simulation
   - **Parameters**: Check/uncheck parameters to include. Adjust bounds using the spin boxes.
   - **Samples (N)**: Set the base sample count (128-1024; higher = more accurate but slower)
   - **KPIs**: Select which output metrics to analyze
4. Click **Run Analysis**. A progress bar shows completion.
5. Results appear as:
   - A horizontal bar chart comparing S1 and ST for each parameter
   - A table of exact Sobol index values with confidence intervals
   - A KPI selector dropdown to switch between output metrics

---

## Running from the Python API

### LP-Level Analysis

```python
from esfex.sensitivity.engine import (
    SensitivityEngine,
    SobolResult,
    get_lp_parameters,
)

# Auto-detect parameters from LP file
lp_path = "results/master_problem.lp"
parameters = get_lp_parameters(lp_path)

print(f"Found {len(parameters)} parameters:")
for p in parameters:
    print(f"  {p.name} ({p.category}): [{p.lower_bound}, {p.upper_bound}]")

# Create engine
engine = SensitivityEngine(
    mode="lp",
    parameters=parameters,
    kpi_names=["total_cost", "inv_gen_total", "inv_bat_total", "curtailment", "load_shedding"],
    n_base_samples=256,
)

print(f"Total evaluations required: {engine.n_evaluations}")

# Run with progress callback
def on_progress(current, total, message):
    if current % 100 == 0:
        print(f"  [{current}/{total}] {message}")

results = engine.run_lp_analysis(lp_path, progress_callback=on_progress)

# Display results
for kpi_name in results.kpi_names:
    print(f"\n--- {kpi_name} ---")
    print(f"{'Parameter':<30} {'S1':>8} {'ST':>8} {'S1_conf':>8} {'ST_conf':>8}")
    for i, param in enumerate(results.parameters):
        s1 = results.S1[kpi_name][i]
        st = results.ST[kpi_name][i]
        s1c = results.S1_conf[kpi_name][i]
        stc = results.ST_conf[kpi_name][i]
        print(f"{param:<30} {s1:8.4f} {st:8.4f} {s1c:8.4f} {stc:8.4f}")

# Export to CSV
results.to_csv("sensitivity_results.csv")
```

### Config-Level Analysis

```python
from esfex.sensitivity.engine import (
    SensitivityEngine,
    get_config_parameters,
)

# Use predefined config-level parameters
parameters = get_config_parameters()

# Or use custom parameters (see above)
# parameters = custom_params

engine = SensitivityEngine(
    mode="config",
    parameters=parameters,
    kpi_names=["total_cost", "inv_gen_total", "inv_bat_total", "curtailment", "load_shedding"],
    n_base_samples=64,  # Lower for config-level (each evaluation is expensive)
)

print(f"Total evaluations: {engine.n_evaluations}")
# With 9 parameters and N=64: 64 * (2*9 + 2) = 1,280 full simulations

results = engine.run_config_analysis(
    base_config_path="island_system.yaml",
    output_dir="results/sensitivity/",
    progress_callback=on_progress,
)

results.to_csv("config_sensitivity_results.csv")
```

---

## Interpreting Results

### Sobol Indices

| Index | Range | Interpretation |
|-------|-------|----------------|
| S1 ~ 0 | [0, 1] | Parameter has negligible direct effect on the output |
| S1 ~ 0.3 | [0, 1] | Parameter explains ~30% of output variance directly |
| S1 ~ 0.5 | [0, 1] | Parameter dominates; explains ~50% of output variance |
| S1 ~ 1.0 | [0, 1] | Parameter almost entirely determines the output |
| ST > S1 | [0, 1] | Significant interaction effects with other parameters |
| ST ~ S1 | [0, 1] | Minimal interaction effects; parameter acts independently |

### Example Output

```
--- total_cost ---
Parameter                         S1       ST   S1_conf  ST_conf
Fuel Cost                       0.4523   0.5187   0.0312   0.0298
RE Investment Cost               0.2156   0.2834   0.0287   0.0265
Storage Investment Cost          0.1478   0.2012   0.0234   0.0221
Demand Growth                    0.0987   0.1456   0.0198   0.0187
Carbon Price                     0.0456   0.0823   0.0156   0.0143
Maintenance Cost                 0.0234   0.0456   0.0123   0.0112
Discount Rate                    0.0123   0.0345   0.0098   0.0089

--- re_penetration ---
Parameter                         S1       ST   S1_conf  ST_conf
RE Investment Cost               0.3812   0.4523   0.0298   0.0287
Storage Investment Cost          0.2987   0.3512   0.0276   0.0265
Fuel Cost                       0.1523   0.1987   0.0234   0.0221
Carbon Price                     0.0876   0.1234   0.0198   0.0187
Demand Growth                    0.0534   0.0876   0.0156   0.0143
```

### Reading the Results

- **Total cost** is most sensitive to fuel cost (S1 = 0.45), which directly drives thermal generation expenditure. The ST-S1 gap (0.52 vs 0.45) indicates interaction with other parameters (e.g., higher fuel cost makes RE investment more attractive).

- **RE penetration** is most sensitive to RE investment cost (S1 = 0.38) and storage investment cost (S1 = 0.30), together explaining ~68% of variance. The ST > S1 gap reflects solar-storage complementarity.

- **Carbon price** has moderate direct effect on RE penetration (S1 = 0.09) but larger total effect (ST = 0.12), indicating interaction with fuel and investment costs.

### Tornado Diagram

The GUI displays a tornado-style horizontal bar chart with:

- Blue bars for first-order indices (S1) — direct effects
- Orange bars for total-order indices (ST) — direct + interaction effects
- Error bars showing confidence intervals

Parameters are sorted by ST (largest at top). A wide gap between blue and orange bars indicates strong interaction effects.

---

## Available KPIs

| KPI | Description | Units |
|-----|-------------|-------|
| `total_cost` | NPV of total system cost (investment + operations) | $ |
| `inv_gen_total` | Total generation investment across all years | MW |
| `inv_bat_total` | Total storage investment across all years | MW + MWh |
| `curtailment` | Total RE curtailment across all years | MWh |
| `load_shedding` | Total unserved energy (loss of load) across all years | MWh |

---

## Computational Cost

Total model evaluations: N x (2D + 2), where N = `n_base_samples` and D = number of parameters.

| Parameters (D) | Samples (N) | Evaluations | LP-Level Time | Config-Level Time |
|----------------|-------------|-------------|---------------|-------------------|
| 5 | 128 | 1,536 | ~2 min | ~26 hours |
| 5 | 256 | 3,072 | ~4 min | ~51 hours |
| 5 | 1024 | 12,288 | ~15 min | ~205 hours |
| 9 | 64 | 1,280 | ~2 min | ~21 hours |
| 9 | 128 | 2,560 | ~3 min | ~43 hours |
| 9 | 256 | 5,120 | ~6 min | ~85 hours |

LP-level times assume ~0.1 seconds per solve. Config-level times assume ~1 minute per full simulation (small system, 5 years).

### Choosing Sample Size

- **N = 64**: Minimum for meaningful Sobol indices. Confidence intervals will be wide. Use only for initial screening with config-level analysis.
- **N = 128-256**: Good balance of accuracy and computation. Recommended for most studies.
- **N = 512-1024**: High accuracy with tight confidence intervals. Use for publication-quality results.
- **N > 1024**: Rarely needed unless dealing with many parameters (D > 10) or highly nonlinear responses.

### Parallel Execution

The current implementation is sequential, but you can parallelize by splitting the sample matrix:

```python
# Generate all samples
samples = engine.generate_samples()

# Split for 4 workers
n_per_worker = len(samples) // 4
for worker_id in range(4):
    start = worker_id * n_per_worker
    end = start + n_per_worker
    worker_samples = samples[start:end]
    # Save and distribute to worker machines
```

---

## Common Parameters to Test

### Economic Parameters

| Parameter | Typical Range | Rationale |
|-----------|--------------|-----------|
| Fuel cost (diesel/gas) | [0.5x, 3.0x] | Oil/gas prices are highly volatile |
| RE investment cost (solar/wind) | [0.4x, 1.5x] | Learning curves drive cost reductions |
| Battery investment cost | [0.3x, 2.0x] | Battery costs declining rapidly |
| Discount rate | [0.5x, 2.0x] | Different stakeholders use different rates |

### Demand Parameters

| Parameter | Typical Range | Rationale |
|-----------|--------------|-----------|
| Demand growth rate | [0.5x, 2.0x] | Economic growth uncertainty |
| EV adoption rate | [0.5x, 3.0x] | Electrification uncertainty |

### Policy Parameters

| Parameter | Typical Range | Rationale |
|-----------|--------------|-----------|
| Carbon price | [0x, 5.0x] | Policy uncertainty (carbon tax may or may not be implemented) |
| RE target | [0.8x, 1.2x] | Political ambition varies |

### Resource Parameters

| Parameter | Typical Range | Rationale |
|-----------|--------------|-----------|
| RE availability | [0.8x, 1.2x] | Climate variability affects capacity factors |

---

## Best Practices

1. **Start with LP-level**: Run LP-level analysis first with N=256 to quickly identify the most important parameters. Then use config-level analysis with fewer parameters and lower N to refine.

2. **Check convergence**: Run the analysis with increasing N (64, 128, 256) and verify that Sobol indices stabilize. If they change significantly between N=128 and N=256, you need more samples.

3. **Interpret confidence intervals**: If `S1_conf` is larger than `S1`, the index is unreliable. Increase N or reduce the number of parameters.

4. **Sum check**: The sum of all S1 values should be close to 1.0 for linear models. For nonlinear models, the sum may be less than 1.0 (indicating interaction effects) or occasionally greater (numerical artifacts at low N).

5. **Focus resources**: Parameters with S1 < 0.05 and ST < 0.10 have negligible influence. Consider fixing them at base-case values in detailed studies to reduce computational burden.

6. **Report both S1 and ST**: S1 captures direct effects; ST captures total effects including interactions. A parameter with low S1 but high ST is not important by itself but significantly modifies the effect of other parameters.

7. **Connect to stochastic analysis**: Parameters with high ST values are natural candidates for stochastic scenario dimensions. If fuel cost has ST = 0.52, it warrants scenario analysis (see the [Stochastic Programming tutorial](stochastic.md)).

---

## Key Takeaways

1. **Global analysis**: Unlike one-at-a-time sweeps, Sobol indices capture parameter interactions and provide a complete picture of parameter importance.
2. **Prioritization**: Focus detailed modeling and data collection on the most influential parameters. Parameters with low Sobol indices can use rough estimates without significantly affecting results.
3. **Robustness**: Parameters with high ST warrant uncertainty analysis through stochastic scenarios or robust optimization.
4. **Trade-off**: More samples yield more accurate indices but require more computation. LP-level analysis provides fast screening; config-level provides definitive results.
5. **Decision support**: Sensitivity results help stakeholders understand which uncertainties matter most for their investment decisions and where to focus risk mitigation efforts.
