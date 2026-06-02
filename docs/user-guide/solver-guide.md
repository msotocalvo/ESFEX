# Solver Guide

## Supported Solvers

ESFEX supports seven optimization solvers through JuMP [**[20]**](../reference/bibliography.md#ref20) (Julia for Mathematical Programming):

| Solver | License | LP | MIP | QP | Speed | Recommended For |
|--------|---------|:--:|:---:|:--:|-------|-----------------|
| **HiGHS** [**[21]**](../reference/bibliography.md#ref21) | Open source (MIT) | Yes | Yes | Yes | Fast | Default; general-purpose; no license needed |
| **Gurobi** | Commercial (free academic) | Yes | Yes | Yes | Fastest | Large-scale MIP; best performance and support |
| **CPLEX** | Commercial (free academic) | Yes | Yes | Yes | Fast | Enterprise/industrial; IBM ecosystem |
| **SCIP** | Open source (Apache 2.0) | Yes | Yes | No | Moderate | Research; constraint programming features |
| **Xpress** | Commercial (free academic) | Yes | Yes | Yes | Fast | Alternative commercial solver |
| **CBC** | Open source (EPL) | Yes | Yes | No | Moderate | Alternative open-source MIP |
| **GLPK** | Open source (GPL) | Yes | Yes | No | Slow | Small problems; educational use; fallback |

### Solver Selection Guidance

- **Start with HiGHS**: Free, fast, and handles the vast majority of ESFEX models well. Default solver; no additional installation required.
- **Use Gurobi for large models**: Best performance for large multi-year, multi-node planning models with MIP formulations. Free academic licenses available.
- **Use CPLEX if available**: Similar performance to Gurobi; preferred if your institution already has an IBM CPLEX license.
- **Avoid GLPK for production**: Significantly slower with weaker numerical handling. Use only for small test cases or when no other solver is available.


---


## Configuration

### YAML Configuration

```yaml
solver:
  name: highs              # Solver name (lowercase)
  threads: 4               # Number of parallel threads
  time_limit: 3600         # Maximum solve time in seconds (0 = unlimited)
  gap: 0.01                # MIP optimality gap tolerance (1%)
  verbose: false           # Show solver output in console
  scale_constraints: true  # Enable constraint scaling
  options:                 # Solver-specific advanced options
    presolve: choose
    solver_method: choose
    parallel: choose
    run_crossover: 'on'
    primal_feasibility_tolerance: 1.0e-07
    dual_feasibility_tolerance: 1.0e-07
    ipm_optimality_tolerance: 1.0e-08
    simplex_iteration_limit: 2147483647
    simplex_scale_strategy: choose
```

### Configuration Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | `"highs"` | Solver name. One of: `highs`, `gurobi`, `cplex`, `cbc`, `glpk`, `scip`, `xpress` |
| `threads` | int | `4` | Number of parallel threads for the solver |
| `time_limit` | int | `10800` | Maximum solve time in seconds. 0 means unlimited |
| `gap` | float | `0.01` | MIP relative optimality gap (0.01 = 1%). Ignored for pure LP |
| `verbose` | bool | `false` | If `true`, prints full solver output to the console |
| `scale_constraints` | bool | `true` | Enable internal constraint scaling for numerical stability |
| `options` | dict | `{}` | Solver-specific options (see Advanced Options below) |

### CLI Override

Override the solver from the command line without modifying the YAML file:

```bash
esfex run -c config.yaml -s gurobi
```

### Checking Available Solvers

```bash
esfex info
```

Or programmatically:

```python
from esfex.config.solver import detect_available_solvers

solvers = detect_available_solvers()
for name, available in solvers.items():
    print(f"{name}: {'Available' if available else 'Not found'}")
```

Detection uses lightweight Python-side checks (companion package importability) and does not require starting the Julia runtime.


---


## Solver Installation

### HiGHS (Default)

Bundled with the `HiGHS.jl` Julia package; no additional installation needed. HiGHS [**[21]**](../reference/bibliography.md#ref21) implements a high-performance dual revised simplex method with parallelization support.

**Strengths**: Fast LP and MIP performance, MIT license, actively maintained, good numerical stability.

### Gurobi

Commercial solver with free academic licenses.

1. **Download**: Get the installer from [gurobi.com](https://www.gurobi.com/downloads/)
2. **Install**: Run the installer for your platform
3. **License**: Obtain a license:
   - **Academic**: Free license at [gurobi.com/academia](https://www.gurobi.com/academia/academic-program-and-licenses/)
   - **Commercial**: Contact Gurobi sales
4. **Activate license**: Run `grbgetkey <your-license-key>`
5. **Install Julia package**:
   ```julia
   import Pkg
   Pkg.add("Gurobi")
   ```
6. **Install Python companion** (for solver detection):
   ```bash
   pip install gurobipy
   ```

**Strengths**: Fastest solver for large-scale MIP, excellent numerical handling, deterministic results, comprehensive logging.

**Environment variables**: Ensure `GUROBI_HOME` and `GRB_LICENSE_FILE` are set if Gurobi is installed in a non-default location.

### CPLEX

Commercial solver (IBM) with free academic licenses.

1. **Download**: Get CPLEX from [IBM Academic Initiative](https://www.ibm.com/academic)
2. **Install**: Run the CPLEX installer
3. **Set environment variable**:
   ```bash
   export CPLEX_STUDIO_BINARIES=/path/to/cplex/bin/x86-64_linux
   ```
4. **Install Julia package**:
   ```julia
   import Pkg
   Pkg.add("CPLEX")
   ```
5. **Install Python companion** (for solver detection):
   ```bash
   pip install cplex
   ```

**Strengths**: Industrial-grade robustness, strong MIP performance, good integration with IBM tools.

### CBC

Open-source MIP solver (Coin-or Branch and Cut).

1. **Install Julia package**:
   ```julia
   import Pkg
   Pkg.add("Cbc")
   ```
2. **Optional Python companion**:
   ```bash
   pip install cylp
   ```

**Strengths**: Free, no license needed, decent MIP performance for moderate-size problems.

**Weaknesses**: Slower than HiGHS for most problems, less actively developed.

### GLPK

Free LP/MIP solver (GNU Linear Programming Kit). Bundled with ESFEX's Julia project; no additional installation needed.

**Strengths**: Always available, simple, well-documented.

**Weaknesses**: Significantly slower than all other solvers, limited numerical precision, poor scaling for large problems. Not recommended for production simulations.

### SCIP

Open-source solver with strong MIP and constraint programming capabilities.

1. **Install Julia package**:
   ```julia
   import Pkg
   Pkg.add("SCIP")
   ```
2. **Optional Python companion**:
   ```bash
   pip install pyscipopt
   ```

**Strengths**: Research-grade solver, constraint programming support, extensible.

### Xpress

Commercial solver (FICO).

1. **Download**: Get Xpress from [FICO](https://www.fico.com/en/products/fico-xpress-optimization)
2. **Install** and activate the license
3. **Install Julia package**:
   ```julia
   import Pkg
   Pkg.add("Xpress")
   ```
4. **Optional Python companion**:
   ```bash
   pip install xpress
   ```

**Strengths**: Fast LP and MIP, good numerical handling.


---


## When to Use LP vs MIP

| Simulation Component | Formulation | When Used |
|---------------------|-------------|-----------|
| Master problem (capacity expansion) | LP | Always (pure LP since retirement refactoring) |
| Operational dispatch (economic dispatch) | LP | `simulation_mode: development` |
| Unit commitment | MIP | `simulation_mode: unit_commitment` |

### LP (Linear Programming)

Continuous variables only; solved to exact optimality regardless of the `gap` setting.

- **Use for**: Capacity expansion planning, economic dispatch
- **Solver choice**: HiGHS is excellent for LP; all solvers handle LP well
- **Performance**: Linear in problem size; even large models solve in seconds to minutes

### MIP (Mixed-Integer Programming)

Includes binary or integer variables (e.g., unit commitment on/off decisions). Computationally harder; may not reach exact optimality within the time limit.

- **Use for**: Unit commitment with binary start-up/shut-down decisions
- **Solver choice**: Gurobi > CPLEX > HiGHS > CBC > SCIP > GLPK
- **Performance**: Exponential worst-case; large models may need significant time and the `gap` parameter controls the acceptable optimality tolerance

The master problem uses a pure LP formulation. Binary life-extension variables were replaced with age-based retirement logic, eliminating MIP complexity from the planning problem.


---


## Performance Tuning

### Thread Count

More threads help for MIP (branch-and-bound parallelization) but have diminishing returns for LP (only IPM benefits). Recommended settings:

| Mode | Recommended Threads | Notes |
|------|-------------------|-------|
| Economic dispatch (LP) | 2-4 | Simplex is mostly single-threaded; IPM benefits from parallelism |
| Unit commitment (MIP) | 4-8 | Branch-and-bound benefits from more threads |
| Master problem (LP) | 2-4 | Similar to economic dispatch |
| Very large models (10+ nodes, 25 years) | 8-16 | More threads help with presolve and barrier method |

ESFEX reserves 2 system threads by default. You can check the recommended thread count:

```python
from esfex.config.solver import get_available_threads
print(f"Recommended threads: {get_available_threads()}")
```

### Time Limits

| Model Size | Suggested Limit | Notes |
|-----------|----------------|-------|
| Single node, 1 year | 300 s | Should solve in seconds |
| 3-5 nodes, 10 years | 1,800 s | Moderate problem size |
| 10+ nodes, 25 years | 3,600-7,200 s | Large problem; may need relaxed gap |
| MIP unit commitment | 600-1,800 s per window | Per rolling-horizon window |

### MIP Gap

The solver stops when:

$$\frac{|BestBound - BestSolution|}{|BestSolution|} \leq gap$$

| Gap Value | Meaning | Speed | Accuracy |
|-----------|---------|-------|----------|
| 0.001 (0.1%) | Very tight | Slow | High |
| 0.01 (1%) | Good balance | Moderate | Good |
| 0.02 (2%) | Relaxed | Fast | Acceptable |
| 0.05 (5%) | Very relaxed | Very fast | Approximate |

For LP models, the solver always reaches exact optimality regardless of this setting.

### LP Method Selection

| Method | Best For | Notes |
|--------|----------|-------|
| Dual simplex | Default choice | Fast for most problems; warm-starts well |
| Primal simplex | Problems with many constraints | Alternative when dual simplex struggles |
| Barrier / IPM [**[4]**](../reference/bibliography.md#ref4) | Large problems | Parallelizes well; may need crossover for exact vertex solution |
| Concurrent | When unsure | Runs multiple methods in parallel (Gurobi only) |

To select a specific method (example for HiGHS):

```yaml
solver:
  name: highs
  options:
    solver_method: ipm    # Use interior point method
    run_crossover: 'on'   # Enable crossover to vertex solution
```


---


## Advanced Solver Options

Each solver exposes tunable parameters through the `options` dictionary.

### HiGHS Options

| Option | Type | Values | Default | Description |
|--------|------|--------|---------|-------------|
| `presolve` | combo | `off`, `on`, `choose` | `choose` | Presolve reduction |
| `solver_method` | combo | `choose`, `simplex`, `ipm` | `choose` | LP algorithm |
| `parallel` | combo | `choose`, `off`, `on` | `choose` | Parallel computation |
| `run_crossover` | combo | `off`, `on` | `on` | Crossover after barrier |
| `primal_feasibility_tolerance` | float | 1e-10 to 1.0 | 1e-7 | Primal feasibility tolerance |
| `dual_feasibility_tolerance` | float | 1e-10 to 1.0 | 1e-7 | Dual feasibility tolerance |
| `ipm_optimality_tolerance` | float | 1e-12 to 1.0 | 1e-8 | IPM optimality tolerance |
| `simplex_iteration_limit` | int | 0 to 2,147,483,647 | 2,147,483,647 | Max simplex iterations |
| `simplex_scale_strategy` | combo | `off`, `choose`, `forced_equilibration`, `mean_equilibration`, `max_equilibration`, `max_value_0`, `max_value_1` | `choose` | Scaling strategy |

### Gurobi Options

| Option | Type | Values | Default | Description |
|--------|------|--------|---------|-------------|
| `method` | combo | `auto`, `primal_simplex`, `dual_simplex`, `barrier`, `concurrent` | `auto` | LP method |
| `presolve` | combo | `auto`, `off`, `conservative`, `aggressive` | `auto` | Presolve level |
| `crossover` | combo | `auto`, `off` | `auto` | Crossover after barrier |
| `numeric_focus` | combo | `auto`, `moderate`, `aggressive` | `auto` | Numerical precision focus |
| `scale_flag` | combo | `auto`, `off`, `moderate`, `aggressive` | `auto` | Scaling level |
| `bar_conv_tol` | float | 1e-12 to 1.0 | 1e-8 | Barrier convergence tolerance |
| `feasibility_tol` | float | 1e-9 to 1e-2 | 1e-6 | Feasibility tolerance |
| `optimality_tol` | float | 1e-9 to 1e-2 | 1e-6 | Optimality tolerance |
| `heuristics` | float | 0.0 to 1.0 | 0.05 | Time fraction for MIP heuristics |
| `iteration_limit` | int | 0 to 2,147,483,647 | 2,147,483,647 | Max iterations |

### CPLEX Options

| Option | Type | Values | Default | Description |
|--------|------|--------|---------|-------------|
| `lp_method` | combo | `auto`, `primal_simplex`, `dual_simplex`, `barrier`, `network` | `auto` | LP method |
| `presolve` | combo | `on`, `off` | `on` | Presolve |
| `numerical_emphasis` | combo | `off`, `on` | `off` | Numerical emphasis |
| `scale` | combo | `equilibration`, `off`, `aggressive` | `equilibration` | Scaling |
| `feasibility_tol` | float | 1e-9 to 1e-1 | 1e-6 | Feasibility tolerance |
| `optimality_tol` | float | 1e-9 to 1e-1 | 1e-6 | Optimality tolerance |
| `barrier_conv_tol` | float | 1e-12 to 1e-1 | 1e-8 | Barrier convergence tolerance |
| `mip_emphasis` | combo | `balanced`, `feasibility`, `optimality`, `best_bound`, `hidden_feasibility` | `balanced` | MIP solving emphasis |

### GLPK Options

| Option | Type | Values | Default | Description |
|--------|------|--------|---------|-------------|
| `msg_lev` | combo | `off`, `errors`, `normal`, `verbose` | `normal` | Message verbosity |
| `meth` | combo | `primal_simplex`, `dual_simplex`, `dual_primal` | `primal_simplex` | LP method |
| `presolve` | combo | `off`, `on` | `on` | Presolve |
| `tol_bnd` | float | 1e-12 to 1e-1 | 1e-7 | Primal tolerance |
| `tol_dj` | float | 1e-12 to 1e-1 | 1e-7 | Dual tolerance |
| `tol_piv` | float | 1e-12 to 1.0 | 1e-10 | Pivot tolerance |
| `it_lim` | int | 0 to 2,147,483,647 | 2,147,483,647 | Iteration limit |
| `mip_gap` | float | 0.0 to 1.0 | 0.0 | MIP gap |

### CBC Options

| Option | Type | Values | Default | Description |
|--------|------|--------|---------|-------------|
| `logLevel` | combo | `off`, `minimal`, `normal`, `verbose` | `minimal` | Log level |
| `primalTolerance` | float | 1e-10 to 1.0 | 1e-7 | Primal tolerance |
| `dualTolerance` | float | 1e-10 to 1.0 | 1e-7 | Dual tolerance |
| `ratioGap` | float | 0.0 to 1.0 | 0.0 | MIP gap |

### SCIP Options

| Option | Type | Values | Default | Description |
|--------|------|--------|---------|-------------|
| `display/verblevel` | combo | `off`, `errors`, `warnings`, `normal`, `full` | `off` | Verbosity |
| `presolving/maxrounds` | combo | `off`, `default`, `aggressive` | `default` | Presolve rounds |
| `separating/maxrounds` | combo | `off`, `default`, `aggressive` | `default` | Cut separation rounds |
| `numerics/feastol` | float | 1e-12 to 1e-1 | 1e-6 | Feasibility tolerance |
| `numerics/dualfeastol` | float | 1e-12 to 1e-1 | 1e-7 | Dual feasibility tolerance |
| `lp/scaling` | combo | `off`, `on` | `on` | LP scaling |

### Xpress Options

| Option | Type | Values | Default | Description |
|--------|------|--------|---------|-------------|
| `OUTPUTLOG` | combo | `off`, `on` | `off` | Output log |
| `PRESOLVE` | combo | `off`, `on` | `on` | Presolve |
| `DEFAULTALG` | combo | `auto`, `dual_simplex`, `primal_simplex`, `barrier` | `auto` | LP algorithm |
| `SCALING` | combo | `off`, `row_col`, `aggressive` | `row_col` | Scaling |
| `FEASTOL` | float | 1e-12 to 1e-1 | 1e-6 | Feasibility tolerance |
| `OPTIMALITYTOL` | float | 1e-12 to 1e-1 | 1e-6 | Optimality tolerance |
| `BARGAPSTOP` | float | 1e-12 to 1.0 | 1e-8 | Barrier gap stop |


---


## Numerical Stability

Optimization models can have wide coefficient ranges due to mixing different physical quantities and cost scales:

| Quantity | Typical Range | Units |
|----------|--------------|-------|
| Generator fuel costs | 3-100 | $/MWh |
| Fixed/maintenance costs | 1-50 | $/MWh |
| Investment costs | 100,000-2,000,000 | $/MW |
| Penalty coefficients | 100-10,000,000 | $/MWh or $/MW |
| Power outputs | 0-1,000 | MW |
| Energy capacity | 0-10,000 | MWh |

This creates a coefficient range spanning 6-9 orders of magnitude, which can cause numerical difficulties.

### Scaling Strategy

HiGHS uses automatic scaling by default. Other solvers also enable scaling. If you encounter numerical issues, try:

**HiGHS**:
```yaml
solver:
  name: highs
  options:
    simplex_scale_strategy: max_equilibration    # Aggressive scaling (value 4)
```

**Gurobi**:
```yaml
solver:
  name: gurobi
  options:
    scale_flag: aggressive     # Aggressive scaling
    numeric_focus: aggressive  # Prioritize numerical accuracy
```

**CPLEX**:
```yaml
solver:
  name: cplex
  options:
    scale: aggressive
    numerical_emphasis: 'on'
```

### Guidelines for Numerical Stability

1. **Keep penalty values reasonable**: Penalties above 10,000,000 can cause solver stalls or numerical warnings. The recommended range is 100-1,000,000.

2. **Check coefficient ranges**: Ensure that the ratio between the largest and smallest non-zero coefficients is less than 10^6.

3. **Enable presolve**: Presolve reduces problem size and can improve numerical properties. It is on by default for all solvers.

4. **Use appropriate tolerances**: The default tolerances (1e-7 for primal/dual, 1e-8 for barrier) are suitable for most models. Only tighten them if you observe solution quality issues.

5. **Monitor solver output**: Enable `verbose: true` for solver diagnostics.


---


## Common Solver Errors and Troubleshooting

### Solver Stall (No Progress)

| Symptom | Cause | Fix |
|---------|-------|-----|
| Solver runs for hours with no improvement | Penalty too high (e.g., `fre_penalty = 600000000`) | Reduce penalty coefficients to < 1,000 |
| Simplex cycling | Degenerate problem | Switch to barrier method: `solver_method: ipm` |
| MIP not finding feasible solutions | Tight constraints | Increase `time_limit`, relax `gap`, or use a faster solver |

### Infeasible Model

```
Model status: INFEASIBLE
```

No solution satisfies all constraints simultaneously. Common causes:

1. **Conflicting constraints**: Demand exceeds available generation capacity plus loss-of-load penalty budget. Check that demand data is correct and generators have sufficient capacity.

2. **Reserve requirements too high**: Static or dynamic reserve requirements may be impossible to meet. Try reducing `reserve_static` or `reserve_dynamic` values.

3. **Missing fuel supply**: Generators may not have fuel available. Check fuel entry points and supply constraints.

4. **Debug strategy**: Enable verbose output (`verbose: true`) and check which constraints are binding. Some solvers (Gurobi, CPLEX) can compute an Irreducible Infeasible Subsystem (IIS) to identify conflicting constraints.

### Unbounded Model

```
Model status: UNBOUNDED
```

The objective can decrease without limit, typically indicating a modeling error:

1. **Variables without upper bounds**: Check that all power output variables have appropriate capacity bounds.
2. **Missing constraints**: Ensure generator output is constrained by rated power and availability.
3. **Sign errors**: Verify that costs are positive and penalties are positive.

### Numerical Warnings

```
WARNING: Numerical issues detected
```

The solver encountered precision issues during solution:

1. **Wide coefficient range**: Check penalty/cost ratios. Reduce the largest penalties.
2. **Enable scaling**: Set `simplex_scale_strategy: max_equilibration` for HiGHS.
3. **Increase tolerance**: Slightly relax feasibility tolerances if the solution is acceptable.
4. **Switch method**: Try barrier method instead of simplex (or vice versa).

### Very Slow MIP

If MIP problems (unit commitment) are excessively slow:

1. **Increase gap**: Set `gap: 0.02` or `gap: 0.05` to accept near-optimal solutions
2. **Reduce problem size**: Use fewer rolling horizon hours, or switch to `development` mode (LP)
3. **Use a better solver**: Switch from GLPK/CBC to HiGHS or Gurobi
4. **Reduce binary variables**: ESFEX's master problem is pure LP. MIP is only used in unit commitment mode

### Out of Memory

Large multi-year, multi-node models can consume significant memory:

1. **Reduce temporal resolution**: Use `resolution_hours: 6` instead of `resolution_hours: 1`
2. **Reduce rolling horizon window**: Use `rolling_horizon_hours: 24` instead of `48`
3. **Fewer representative days**: Reduce `representative_days` in master problem config
4. **Limit threads**: Each thread uses memory for its own copy of the problem data. Reducing thread count reduces memory.


---


## Memory Considerations for Large Models

| Factor | Memory Impact | Typical Contribution |
|--------|--------------|---------------------|
| Number of nodes | Quadratic (adjacency matrix) | Moderate |
| Number of generators | Linear per node | Moderate |
| Number of hours | Linear | Large (dominant factor) |
| Number of years (master problem) | Linear | Moderate |
| Number of binary variables (MIP) | Adds branch-and-bound tree | Large for MIP |

Approximate memory requirements:

| Model Size | LP Memory | MIP Memory |
|-----------|-----------|------------|
| 1 node, 1 year, 48h windows | ~100 MB | ~200 MB |
| 5 nodes, 10 years | ~500 MB | ~2 GB |
| 10 nodes, 25 years | ~2 GB | ~8 GB |
| 50 nodes, 25 years | ~10 GB | ~40+ GB |


---


## Solver Log Interpretation

### Enabling Verbose Output

```yaml
solver:
  verbose: true
```

### HiGHS Log Example

```
Running HiGHS 1.7.0
Solving LP
Using EKK dual simplex solver - serial
  Iteration        Objective     Infeasibilities  Num
          0    0.0000000000e+00 Pr: 1460(3.5e+04) 0s
       1200    8.5432100000e+06 Pr: 0(0)           2s
Solving report
  Status            Optimal
  Primal bound      8.5432100000e+06
  Dual bound        8.5432100000e+06
  Solution status   feasible
  Timing            1.85 (total)
```

Key information:
- **Iteration count**: Higher counts may indicate scaling issues
- **Primal infeasibilities** (`Pr:`): Should reach 0 at optimality. If stuck above 0, there may be numerical issues
- **Status**: `Optimal` = solved successfully, `Infeasible` = no feasible solution, `Time limit` = stopped early

### Gurobi Log Example

```
Optimize a model with 15000 rows, 25000 columns and 75000 nonzeros
Presolve removed 3000 rows and 5000 columns
Presolved: 12000 rows, 20000 columns, 60000 nonzeros

Iteration    Objective       Primal Inf.    Dual Inf.      Time
       0    0.0000000e+00   3.500000e+04   0.000000e+00      0s
    1500    8.5432100e+06   0.000000e+00   0.000000e+00      2s

Solved in 1500 iterations and 1.8 seconds
Optimal objective  8.543210000e+06
```

Key information:
- **Presolve reduction**: Shows how much the model was simplified. Large reductions are good.
- **Primal/Dual Inf.**: Both should reach 0.
- **Iteration count vs. column count**: If iterations >> columns, the model may have numerical issues.

### Understanding Objective Values

The objective value represents total system cost in dollars. Reasonable ranges depend on system size:

| System Size | Typical Objective (per year) |
|------------|----------------------------|
| Small island (50 MW) | $5M - $50M |
| Medium system (500 MW) | $50M - $500M |
| Large system (5 GW) | $500M - $5B |

If the objective is unexpectedly low (e.g., $50 for a real system), this may indicate a modeling error such as free generation from unconstrained variables. If it is unexpectedly high, check for excessive penalty costs driving the solution.
