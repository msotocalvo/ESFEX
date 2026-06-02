# Results Export

## HDF5 Output Structure

One HDF5 file is written per run (`results/esfex_run_{hash}_{date}.h5`). The layout is organised by *kind* of data — static configuration, summary scalars, full time series, multi-system aggregates, and the MGA / SPORES sweep:

```
esfex_run_…h5
├── [Root attributes]              # Metadata + multi-system layout (see below)
├── system_configuration/          # Static system definition (generators,
│                                  #   batteries, technologies, nodes)
├── summary_results/               # Per-year scalars (one row per year)
├── detailed_results/              # Full time series, one scenario per year
│   └── year_YYYY_threshold_0/     # ~30 datasets / groups per scenario
├── demand/                        # Input demand profiles
│   └── year_YYYY_base_demand
├── cost_breakdown/                # Granular cost decomposition per year
│   └── year_YYYY/                 # Cost components as group attrs
├── global/                        # Multi-system aggregates (always present;
│   ├── summary_results/           #   for single-system runs == root summary)
│   ├── cost_breakdown/
│   └── demand/
├── inter_system/                  # Multi-system only — flows between
│   └── detailed_results/          #   subsystems per scenario
│       └── year_YYYY_threshold_0/
│           └── power_flow/{A_to_B}
└── mga/                           # Only when MGA / SPORES enabled
    ├── @method = "mga" | "spores"
    ├── @objectives = [...]        # SPORES menu (empty for MGA runs)
    ├── @num_alternatives
    ├── @slack_fraction
    ├── @optimal_cost
    ├── alternative_0/             # Cost-optimal seed (@objective="cost_optimal")
    │   ├── tech_investment            # (years, techs, nodes)
    │   ├── bat_tech_power_investment  # (years, bat_techs, nodes)
    │   ├── bat_tech_capacity_investment # (years, bat_techs, nodes)
    │   ├── cumulative_gen_capacity    # (years, generators, nodes)
    │   └── re_penetration             # (years,)
    └── alternative_K/             # K = num_alternatives  (MGA) or
                                   #     len(objectives)   (SPORES)
        └── @objective = "hsj_diversity" | "min_total_build" | …
```

**Per-system mirrors have been removed.** Earlier versions wrote a `/systems/{name}/detailed_results/` / `/systems/{name}/summary_results/` mirror per subsystem; the viewer now slices the global block on read using the subsystem root attributes. See [HDF5 Output Schema § File Structure](../reference/hdf5-output-schema.md#file-structure-overview) for the full canonical reference.

### Root Attributes

Always present:

| Attribute | Type | Description |
|-----------|------|-------------|
| `creation_date` | string | ISO 8601 creation timestamp |
| `num_nodes` | int | Number of network nodes |
| `num_years` | int | Number of simulation years |
| `temporal_resolution_hours` | int | Time step (1 = hourly, 3 = 3-hourly, …) |
| `simulation_mode` | string | `"development"` or `"unit_commitment"` |
| `years_range` | string | e.g. `"2025-2049"` |
| `export_type` | string | `"incremental_results"` |
| `export_complete` | bool | Whether the export finished successfully |

For **multi-system runs**, additional root attrs describe how to slice the global block:

| Attribute | Type | Description |
|-----------|------|-------------|
| `num_systems` | int | Number of subsystems |
| `subsystem_names` | string[] | e.g. `["IslaJuventud", "Cuba"]` |
| `subsystem_offsets` | int[] | Starting node index of each subsystem |
| `subsystem_node_counts` | int[] | Number of nodes in each subsystem |

The viewer (see [`results_charts._open_scenario`](../api/bridge-adapters.md) and `_system_node_range`) uses these to derive a per-system view from the global block — e.g. to extract Cuba's nodes from a `(10, T)` global array, it slices `[1:10, :]`.

### Inside `detailed_results/year_YYYY_threshold_0/`

About 30 entries per scenario. Most arrays carry the global node axis as their first dimension; the viewer applies subsystem slicing on read. Names retain underscores and capitalisation as written by the runner.

| Entry | Shape | Units | Description |
|-------|-------|-------|-------------|
| `demand` | (N, T) | MW | Total demand including growth, EV, and rooftop netting |
| `generation/{gen_name}` | (N, T) | MW | Per-generator output (group, one dataset per unit) |
| `gen_status/{gen_name}` | (N, T) | 0/1 | UC mode commitment status |
| `gen_startup/{gen_name}` | (N, T) | 0/1 | Startup events (UC mode) |
| `gen_shutdown/{gen_name}` | (N, T) | 0/1 | Shutdown events (UC mode) |
| `curtailment` | (N, T) | MW | Renewable curtailment per node (post-Phase-2bis: per-node; legacy files had a `(T,)` aggregate) |
| `battery_charge/{bat_name}` | (N, T) | MW | Per-battery charge (group) |
| `battery_discharge/{bat_name}` | (N, T) | MW | Per-battery discharge (group) |
| `battery_soc/{bat_name}` | (N, T) | MWh | Per-battery SOC (group) |
| `battery_capacity_factor/{bat_name}` | (N, T) | unitless | Per-battery CF (group) |
| `battery_lcoe/{bat_name}` | (N, T) | $/MWh | Per-battery LCOE (group) |
| `battery_vallcoe/{bat_name}` | (N, T) | $/MWh | Per-battery value-adjusted LCOE (group) |
| `reserve_static`, `reserve_dynamic` | (N, T) | MW | Reserve provision |
| `loss_of_reserve_static`, `loss_of_reserve_dynamic` | (N, T) | MW | Unmet reserves |
| `loss_load` | (N, T) | MW | Unserved demand |
| `CO2_emissions` | (N, T) | tonnes | Per-node emissions |
| `nodal_electricity_prices` | (N, T) | $/MWh | LMPs per node |
| `electricity_prices` | (T,) | $/MWh | System-wide average |
| `voltage_angle` | (N, T) | radians | Bus angles (DC PF) |
| `power_flow` | (N, N, T') | MW | Pairwise inter-node flows (T' can differ from T) |
| `rooftop_generation` | (T, N) | MW | Behind-the-meter rooftop solar (note: `(T, N)` not `(N, T)`) |
| `EV_charging`, `EV_V2G`, `EV_soc`, `EV_loss` | (N, T) | MW / MWh | EV fleet variables |
| `capacity_factor/{gen}`, `lcoe/{gen}`, `vallcoe/{gen}` | (N, T) | various | Per-generator derived metrics |
| `technology_selling_prices/{system}/{tech}/prices_weights` | (K, 3) | mixed | Revenue analysis (price, MW, timestep) |

Where `N` = `num_nodes`, `T` = hours in the year at `temporal_resolution_hours` cadence (e.g. 2920 for 3-hourly), and `T'` for `power_flow` is the flow-specific time axis (may exceed `T`).

### `summary_results/` (per-year scalars)

| Dataset | Shape | Description |
|---------|-------|-------------|
| `year` | (num_years,) | Simulation year |
| `threshold` | (num_years,) | Threshold iteration (usually 0) |
| `feasible` | (num_years,) | 1 if feasible, 0 if not |
| `total_cost` | (num_years,) | Total annual cost ($) |
| `renewable_penetration` | (num_years,) | RE penetration (0-1) |
| `co2_emissions` | (num_years,) | Annual CO₂ (tonnes) |
| `loss_of_load` | (num_years,) | Total unserved energy (MWh) |
| `n1_security_cost` | (num_years,) | N-1 security cost ($) |

### Virtual Generator/Battery Naming

Virtual units from technology investments appear alongside original units. HDF5 naming order follows the adapter ordering:

1. Original generators (as defined in config)
2. Virtual generators (from technology investments)
3. Original batteries (as defined in config)
4. Virtual batteries (from battery technology investments)

Virtual units are named `Investment {Technology Name}` (e.g., `Investment Solar PV`, `Investment Li-Ion`).


---


## Derived Metrics

Computed post-optimization by `_compute_derived_metrics()`:

| Metric | Formula | Units | Notes |
|--------|---------|-------|-------|
| **LCOE** | (fuel_cost + fixed_cost + maintenance_cost + annualized_capex) / total_generation | $/MWh | Levelized cost of electricity per generator |
| **VALLCOE** | LCOE adjusted for system value (accounts for dispatch timing) | $/MWh | Higher for peakers, lower for baseload |
| **Capacity Factor** | actual_generation / (rated_power * hours) | [0, 1] | Fraction of time at full output |
| **Battery CF** | (total_charge + total_discharge) / (2 * capacity * hours) | [0, 1] | Battery utilization rate |
| **RE Penetration** | RE_generation / total_demand | [0, 1] | Fraction of demand met by renewables |
| **Fuel for Power** | generation / efficiency | MWh-fuel | Primary energy consumed per fuel type |
| **Technology Selling Prices** | Weighted average price at dispatch hours | $/MWh | Revenue per technology |

### Capacity Factor Interpretation

| Range | Interpretation |
|-------|---------------|
| 0.85 - 1.0 | Baseload operation (nuclear, some coal) |
| 0.40 - 0.85 | Mid-merit / high-resource renewable |
| 0.15 - 0.40 | Typical solar PV or moderate wind |
| 0.05 - 0.15 | Peaking unit (gas turbine, diesel) |
| < 0.05 | Rarely dispatched; candidate for retirement |

---


## Export Commands

### CSV Export

One CSV file per dataset, organized in subdirectories:

```bash
esfex export -r results/esfex_run_…h5 -f csv -o results/csv/
```

Output structure mirrors the HDF5 layout, with one CSV per dataset and one folder per group:

```
results/csv/
├── summary/                    # /summary_results/*
│   ├── year.csv
│   ├── total_cost.csv
│   ├── renewable_penetration.csv
│   ├── co2_emissions.csv
│   ├── loss_of_load.csv
│   ├── feasible.csv
│   └── n1_security_cost.csv
├── detailed/                   # /detailed_results/{scenario}/*
│   └── year_2025_threshold_0/
│       ├── generation/
│       │   ├── Solar_PV.csv
│       │   ├── Wind.csv
│       │   └── …                # one CSV per generator
│       ├── curtailment.csv
│       ├── loss_load.csv
│       ├── nodal_electricity_prices.csv
│       ├── electricity_prices.csv
│       ├── battery_charge/
│       │   └── Li_Ion.csv
│       ├── battery_discharge/
│       │   └── Li_Ion.csv
│       ├── CO2_emissions.csv
│       ├── power_flow.csv
│       ├── capacity_factor/
│       ├── lcoe/
│       └── technology_selling_prices/
└── demand/                     # /demand/*
    └── year_2025_base_demand.csv
```

### Excel Export

Single workbook with summary and selected operational data:

```bash
esfex export -r results/esfex_run_…h5 -f excel -o results/report.xlsx
```

Sheets:
- **Summary**: per-year scalars from `/summary_results/` (cost, RE penetration, CO₂, load shed)
- **Generation**: total generation per generator (summed across nodes and timesteps)

### JSON Export

```bash
esfex export -r results/esfex_run_…h5 -f json -o results/data.json
```

Includes metadata and summary arrays. Full hourly time-series data is omitted; use CSV or direct HDF5 access for time-series analysis.


---


## Python API

### Direct HDF5 Access

The scenario key encodes the year and the threshold iteration: `year_2025_threshold_0`. Generation, status and battery data live in groups keyed by unit name (one dataset per generator / battery).

```python
import h5py

with h5py.File("results/esfex_run_…h5", "r") as f:
    # Top-level groups
    print("Groups:", list(f.keys()))

    # Root metadata
    print("Created:        ", f.attrs.get("creation_date"))
    print("Years range:    ", f.attrs.get("years_range"))
    print("Temporal res:   ", f.attrs.get("temporal_resolution_hours"), "h")
    print("Mode:           ", f.attrs.get("simulation_mode"))
    # Multi-system layout (absent for single-system runs)
    if "subsystem_names" in f.attrs:
        names = [n.decode() if isinstance(n, bytes) else str(n)
                 for n in f.attrs["subsystem_names"]]
        offsets = f.attrs["subsystem_offsets"]
        counts = f.attrs["subsystem_node_counts"]
        for n, o, c in zip(names, offsets, counts):
            print(f"  subsystem {n}: nodes [{o}, {o+c}) ({c} node(s))")

    # ── Per-year summary (one row per year) ──
    years = f["summary_results/year"][:]
    costs = f["summary_results/total_cost"][:]
    re_pen = f["summary_results/renewable_penetration"][:]
    for y, c, r in zip(years, costs, re_pen):
        print(f"  {y}: cost=${c:,.0f}  RE={r:.1%}")

    # ── Detailed time series for one year ──
    sc = f["detailed_results/year_2025_threshold_0"]

    # Per-generator output: group with one (N, T) dataset per generator
    for gen_name in list(sc["generation"].keys())[:5]:
        arr = sc["generation"][gen_name][:]   # shape (N, T)
        print(f"  {gen_name}: total = {arr.sum():,.0f} MW·step")

    # Nodal LMP and curtailment
    lmp = sc["nodal_electricity_prices"][:]   # (N, T)
    print(f"  LMP: mean=${lmp.mean():.2f}/MWh  max=${lmp.max():.2f}/MWh")
    curt = sc["curtailment"][:]                # (N, T) for new files,
                                               # (T,) for legacy
    print(f"  Curtailment total: {curt.sum():,.0f} MW·step")

    # Loss of load (note the field name)
    ll = sc["loss_load"][:]                   # (N, T)
    print(f"  Loss of load: {ll.sum():,.0f} MWh")
```

### Plotting Generation Stack

```python
import h5py
import matplotlib.pyplot as plt

with h5py.File("results/esfex_run_…h5", "r") as f:
    sc = f["detailed_results/year_2025_threshold_0"]
    gen_group = sc["generation"]                 # group keyed by unit name

    gen_names = sorted(gen_group.keys())
    # Sum across nodes for each generator → (G, T)
    gen_total = [gen_group[name][:].sum(axis=0) for name in gen_names]

    tres = int(f.attrs.get("temporal_resolution_hours", 1))
    steps_per_week = (24 * 7) // tres            # e.g. 56 steps for 3-hourly

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.stackplot(range(steps_per_week),
                 [g[:steps_per_week] for g in gen_total],
                 labels=gen_names, alpha=0.8)
    ax.set_xlabel(f"Step (× {tres} h)")
    ax.set_ylabel("Generation (MW)")
    ax.set_title("Generation Stack — Week 1 (Year 2025)")
    ax.legend(loc="upper right", fontsize=7, ncol=2)
    plt.tight_layout()
    plt.savefig("generation_stack.png", dpi=150)
```

### Plotting Prices

```python
with h5py.File("results/esfex_run_…h5", "r") as f:
    sc = f["detailed_results/year_2025_threshold_0"]
    lmp = sc["nodal_electricity_prices"][:]    # (N, T)

    tres = int(f.attrs.get("temporal_resolution_hours", 1))
    steps_per_week = (24 * 7) // tres

    fig, ax = plt.subplots(figsize=(14, 4))
    for n in range(lmp.shape[0]):
        ax.plot(lmp[n, :steps_per_week], label=f"Node {n}", alpha=0.6)
    ax.set_xlabel(f"Step (× {tres} h)")
    ax.set_ylabel("Price ($/MWh)")
    ax.set_title("Locational Marginal Prices — Week 1")
    ax.legend(ncol=4, fontsize=8)
    plt.tight_layout()
    plt.savefig("prices.png", dpi=150)
```

### Computing Custom Metrics

```python
with h5py.File("results/esfex_run_…h5", "r") as f:
    sc = f["detailed_results/year_2025_threshold_0"]

    # Total generation per generator (summed across nodes and time)
    gen_group = sc["generation"]
    tres = int(f.attrs.get("temporal_resolution_hours", 1))
    print("Per-generator energy (MWh):")
    gen_total = {}
    for name in sorted(gen_group.keys()):
        arr = gen_group[name][:]               # (N, T)
        mwh = arr.sum() * tres                 # MW · h
        gen_total[name] = mwh
        if mwh > 0:
            print(f"  {name:50s} {mwh:>14,.0f}")

    # Energy not served (ENS) and Loss of Load Events (LOLE)
    ll = sc["loss_load"][:]                    # (N, T)
    demand = sc["demand"][:]                   # (N, T)
    ens = ll.sum() * tres                      # MWh of unserved demand
    total_demand = demand.sum() * tres
    print(f"\nENS:  {ens:,.0f} MWh ({ens/total_demand:.4%} of demand)")

    # Loss-of-load events: count of timesteps with any unserved demand
    lole_events = (ll.sum(axis=0) > 0.01).sum()
    print(f"LOLE: {lole_events} steps  ({lole_events*tres} h)")
```

### Using ResultsExporter

```python
from esfex.io.exporter import ResultsExporter

exporter = ResultsExporter("results/esfex_run_…h5")

# Export to CSV
exporter.to_csv("results/csv/")

# Export to Excel
exporter.to_excel("results/report.xlsx")

# Export to JSON
exporter.to_json("results/data.json")
```

### Using read_results for Full Data Access

```python
from esfex.io.exporter import read_results

results = read_results("results/esfex_run_…h5")

# Access metadata
print(results["metadata"])

# Access scenarios
for scenario_name, data in results["scenarios"].items():
    print(f"\nScenario: {scenario_name}")
    print(f"  Attributes: {data['attrs']}")

    # Access hourly data
    hourly = data["hourly_data"]
    if "generation" in hourly:
        for gen_name, gen_array in hourly["generation"].items():
            print(f"  {gen_name}: shape={gen_array.shape}, "
                  f"total={gen_array.sum():,.0f} MWh")
```

### Using YearResults (Programmatic Runs)

Results are returned directly as Python objects:

```python
from esfex.config.loader import load_config
from esfex.runner import Orchestrator

config = load_config("config.yaml")
orch = Orchestrator(config)
results = orch.run(years=10)

# Access year 1 results
yr1 = results[0]
print(f"Year:           {yr1.year}")
print(f"Objective:      ${yr1.objective:,.0f}")
print(f"RE penetration: {yr1.re_penetration:.1%}")
print(f"Generation:     shape={yr1.gen_output.shape}  (G × N × T)")
print(f"LMPs:           shape={yr1.prices.shape}      (N × T)")
print(f"Load shed:      {yr1.load_shed:,.1f} MWh "
      f"(per-step: yr1.load_shed_array, shape={yr1.load_shed_array.shape})")
print(f"CO2 emissions:  {yr1.emissions:,.0f} tonnes")
```

**Note**: the `YearResults` dataclass exposes both a scalar `load_shed` (total MWh for the year) and a time-series `load_shed_array` (shape `(N, T)`). The same pattern applies to `emissions` (scalar) and `co2_emissions` (`(N, T)` array). Use whichever fits your downstream code.

---


## MGA / SPORES Results

Near-optimal alternatives land in `/mga/` *inside the same results file* — there is no separate `mga_*.h5` file. The group root carries the metadata the viewer needs to pick a colour / label encoding (see [Near-Optimal Alternatives tutorial](../tutorials/mga.md)).

```python
with h5py.File("results/esfex_run_…h5", "r") as f:
    if "mga" not in f:
        print("This run did not enable MGA / SPORES.")
    else:
        mga = f["mga"]
        # Root attributes describe the method and the cost envelope.
        method = mga.attrs.get("method", b"mga")
        method = method.decode() if isinstance(method, bytes) else str(method)
        objectives = [o.decode() if isinstance(o, bytes) else str(o)
                      for o in mga.attrs.get("objectives", [])]
        print(f"Method:       {method}")
        print(f"Objectives:   {objectives or '— (MGA HSJ loop)'}")
        print(f"Optimal cost: ${mga.attrs['optimal_cost']/1e9:.2f}B")
        print(f"Slack:        {mga.attrs['slack_fraction']*100:.1f}%")

        for k in sorted(mga.keys(),
                        key=lambda s: int(s.rsplit("_", 1)[-1])):
            alt = mga[k]
            obj = alt.attrs.get("objective", b"")
            obj = obj.decode() if isinstance(obj, bytes) else str(obj)
            cost = float(alt.attrs["cost"])
            opt = bool(alt.attrs.get("is_optimal", False))
            tag = "★" if opt else " "
            print(f"  {tag} {k:18s} cost=${cost/1e9:.2f}B  objective={obj}")
            # Per-alternative datasets — all shape (years, …, nodes)
            for ds in ("tech_investment", "bat_tech_power_investment",
                       "bat_tech_capacity_investment",
                       "cumulative_gen_capacity", "re_penetration"):
                if ds in alt:
                    print(f"      {ds:30s} {alt[ds].shape}")
```

### What's in each alternative

| Dataset | Shape | Units | Description |
|---------|-------|-------|-------------|
| `tech_investment` | (years, techs, nodes) | MW | New technology power installed per year, tech and node |
| `bat_tech_power_investment` | (years, bat_techs, nodes) | MW | New battery power per year, tech and node |
| `bat_tech_capacity_investment` | (years, bat_techs, nodes) | MWh | New battery energy per year, tech and node |
| `cumulative_gen_capacity` | (years, generators, nodes) | MW | Total deployed capacity per generator over time |
| `re_penetration` | (years,) | fraction | Annual RE-penetration trajectory |

### Per-alternative attributes (Phase 4 export)

| Attribute | Type | Description |
|-----------|------|-------------|
| `alternative_id` | int | 0 = cost-optimal seed, 1..K = non-optimal alternatives |
| `is_optimal` | bool | `True` for `alternative_0` only |
| `cost` | float | Actual system cost ($) — non-optimal alternatives typically saturate the cost cap (1 + slack)·optimal_cost |
| `diversity_objective` | float | Objective value at this alternative (absent for the seed) |
| `objective` | string | Tag identifying which objective produced this alternative: `"cost_optimal"` (seed), `"hsj_diversity"` (any MGA alt), or one of `"min_total_build"`, `"max_tech_equity"`, `"max_regional_equity"`, `"evolutionary_dist"` (SPORES alts). Result files predating Phase 4 fall back to `"cost_optimal"` / `"hsj_diversity"` on read |

See [HDF5 Output Schema § MGA/SPORES Results](../reference/hdf5-output-schema.md#mgaspores-results) for the full attribute catalogue and [Near-Optimal Alternatives](../tutorials/mga.md) for a worked walkthrough of both methods.

---


## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| HDF5 file is empty or very small | Optimization failed or was interrupted | Check console output for error messages; re-run with `--verbose` |
| Missing virtual generators in output | `_rebuild_unit_names` was not called | This was fixed in v0.1.0; update to latest version |
| GUI shows 90%+ load shedding but optimization reports feasible | Virtual units missing from HDF5 export | Ensure unit name lists match adapter ordering (original then virtual) |
| `KeyError` when accessing a dataset | Dataset name may differ between versions | List available keys with `list(f["detailed_results/..."].keys())` |
| Large file size (> 1 GB) | Many years, nodes, or generators | Use compression; data is already gzip-compressed by default |
