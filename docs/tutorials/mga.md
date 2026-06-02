# Near-Optimal Alternatives

The cost-optimal plan answers *what should we build?* with a single deterministic recipe. In practice, multiple investment portfolios sit within a few percent of that cost. **MGA** and **SPORES** systematically map this near-optimal space — turning a point estimate into a portfolio of feasible options that stakeholders can compare on their own terms (cost, equity, footprint, exposure to specific technologies).

ESFEX implements both methods. They share the cost-slack envelope \(Z \leq (1+\varepsilon) C^*\) but answer different questions:

- **[MGA](#mga-classical-hop-skip-jump-loop)** (Modeling to Generate Alternatives [**[8]**](../reference/bibliography.md#ref8)) — runs a single diversity objective \(K\) times. Each iteration penalises investment variables seen in earlier alternatives. Good for "surprise me, show me \(K\) different plans."
- **[SPORES](#sporesper-objective-sweep)** (Spatially-explicit Practically Optimal REsultS [**[7]**](../reference/bibliography.md#ref7)) — solves one alternative per *named objective* (smallest portfolio, technology equity, regional equity, evolutionary distance). Good for "show me the plan that minimises X under the cost cap."

A [decision matrix](#when-to-use-which) at the end of this tutorial helps you choose.

---

## Concept Recap

For every method:

1. Solve the cost-optimal Master Problem \(\to\) \(C^*\), \(x_0^*\).
2. Add the near-optimal constraint \(Z \leq (1 + \varepsilon) \cdot C^*\) to the model. The constraint persists across all subsequent solves.
3. Generate \(K\) alternative plans, each respecting the cost cap.

What changes between the methods is the **objective function** in step 3. The full LP formulation is in [Capacity Expansion § 15](../formulation/capacity-expansion.md#15-mgaspores-near-optimal-alternative-exploration); this tutorial focuses on the user-facing workflow.

---

## MGA — Classical Hop-Skip-Jump Loop

In the HSJ formulation, every iteration \(k\) maximises a single LP objective weighted by **frequency scores** \(\sigma = 1 - 2 \cdot \mathrm{freq}\). The frequency \(\mathrm{freq}_{g,n,y}\) tracks how often investment variable \((g, n, y)\) was non-zero across \(\{x_0^*, \ldots, x_{k-1}^*\}\), so technologies / locations seen in many previous alternatives are pushed out, encouraging the model to find structurally different solutions.

### Step 1: Enable MGA in the YAML

```yaml
master_problem:
  mga:
    enabled: true
    method: mga                # classical Hop-Skip-Jump (default)
    num_alternatives: 10       # generate 10 alternatives beyond the cost-optimal
    slack_fraction: 0.05       # allow up to 5% cost increase
    investment_threshold: 0.1  # 0.1 MW counts as "invested" for HSJ scoring
```

### Step 2: Run

```bash
esfex run my_system.yaml
```

A single Master Problem is built, the cost-optimal seed is solved, the cost cap is added, then the HSJ loop runs 10 times — re-using the same JuMP model and only swapping the objective each iteration.

### Step 3: Read the results

Alternatives land in `results.h5` under `/mga/`:

```python
import h5py

with h5py.File("results/my_system.h5", "r") as f:
    mga = f["mga"]
    print(f"Method: {mga.attrs['method'].decode()}")           # "mga"
    print(f"Cost optimal: ${mga.attrs['optimal_cost']/1e9:.2f}B")
    for k in sorted(mga.keys()):
        alt = mga[k]
        obj = alt.attrs["objective"].decode()
        print(f"  {k}: cost=${alt.attrs['cost']/1e9:.2f}B  objective={obj}")
```

Sample output for a Cuba system with 10 alternatives:

```
Method: mga
Cost optimal: $29.74B
  alternative_0: cost=$29.74B  objective=cost_optimal
  alternative_1: cost=$31.23B  objective=hsj_diversity
  alternative_2: cost=$31.23B  objective=hsj_diversity
  ...
  alternative_10: cost=$31.23B  objective=hsj_diversity
```

Every non-optimal alternative typically saturates the cost cap (the HSJ objective has no incentive to leave cost on the table).

---

## SPORES — Per-Objective Sweep

SPORES replaces the HSJ loop with a *menu* of LP objectives. Each entry produces one alternative under the same cost-slack envelope. ESFEX ships four canonical objectives plus the HSJ score retained as a special case:

| Symbol | Question it answers |
|--------|---------------------|
| `hsj_diversity` | Same as MGA — included for sweeps that want both flavours |
| `min_total_build` | What is the *smallest* near-optimal portfolio? |
| `max_tech_equity` | Can the portfolio be technology-diversified? |
| `max_regional_equity` | Can investments be spatially spread? |
| `evolutionary_dist` | What is the *maximally different* plan from the cost-optimal? |

### Step 1: Enable SPORES in the YAML

```yaml
master_problem:
  mga:
    enabled: true
    method: spores                       # switch from "mga"
    objectives:                          # the menu — one alternative per entry
      - min_total_build
      - max_tech_equity
      - max_regional_equity
      - evolutionary_dist
    slack_fraction: 0.05                 # shared with MGA
    # num_alternatives is ignored under SPORES
```

The schema validates the configuration at load time. Two common mistakes raise `ValueError` immediately:

- `method: spores` with an empty `objectives` list — *"choose at least one of: hsj_diversity, min_total_build, …"*.
- `method: mga` with `objectives` populated — *"objectives is only valid with method='spores'"*.

When `enabled: false`, the validator is bypassed so YAML drafts can keep both fields populated.

### Step 2: Run

```bash
esfex run my_system.yaml
```

The sweep loop runs once per objective. Each call to `apply_spores_objective!` cleans up the previous objective's auxiliary variables / constraints before installing its own, so the JuMP model never accumulates dead aux across the sweep.

### Step 3: Read the results

```python
with h5py.File("results/my_system.h5", "r") as f:
    mga = f["mga"]
    method = mga.attrs["method"].decode()
    objs = [o.decode() for o in mga.attrs["objectives"]]
    print(f"Method: {method}  (objectives: {objs})")
    for k in sorted(mga.keys()):
        alt = mga[k]
        obj = alt.attrs["objective"].decode()
        cost = alt.attrs["cost"] / 1e9
        cost_pct = 100 * (cost - mga.attrs["optimal_cost"]/1e9) / (mga.attrs["optimal_cost"]/1e9)
        print(f"  {k}: cost=${cost:.2f}B  (+{cost_pct:.2f}% vs opt)  objective={obj}")
```

Sample output for the same Cuba system with the four canonical objectives:

```
Method: spores  (objectives: ['min_total_build', 'max_tech_equity',
                              'max_regional_equity', 'evolutionary_dist'])
  alternative_0: cost=$29.74B  (+0.00% vs opt)  objective=cost_optimal
  alternative_1: cost=$30.12B  (+1.28% vs opt)  objective=min_total_build
  alternative_2: cost=$31.23B  (+5.00% vs opt)  objective=max_tech_equity
  alternative_3: cost=$31.23B  (+5.00% vs opt)  objective=max_regional_equity
  alternative_4: cost=$31.23B  (+5.00% vs opt)  objective=evolutionary_dist
```

Notice that `min_total_build` does **not** saturate the cost cap: minimising the build volume tends to keep the system close to the cost-optimal, because the cost-optimal already uses cheap technologies efficiently. The equity and distance objectives, on the other hand, typically saturate the cap — they actively trade cost for spatial / structural diversity.

---

## Viewing Results in the GUI

The Result Viewer's **MGA** section reads `/mga.attrs["method"]` and picks an appropriate visual encoding:

- **Robust Frontier** (top scatter): under MGA, every alternative is blue; under SPORES, each marker is coloured by its objective and the legend names them.
- **Alternative Map** (PCA / t-SNE projection): under MGA, points are coloured by cost using a Viridis ramp; under SPORES, points are coloured by objective (categorical), with the colourbar replaced by a categorical legend.
- **Cluster Tree** (annotated circular dendrogram): the middle annotation ring shows peak-RE share under MGA; under SPORES it shows the objective tag.
- **Decision Factors**, **Composition**, **Pairwise Similarity**, **Spatial Divergence**, **Deployment Pathways** — work identically for both methods; the story they tell does not depend on which generator produced each alternative.

---

## Running From Python

Both methods are accessible from the `MGAAdapter` programmatic interface. The result format is uniform, with a per-alternative `objective` tag so downstream code can dispatch on it.

```python
from esfex.bridge.adapters import MasterProblemAdapter, MGAAdapter
from esfex.config.schema import MGAConfig, SporesObjective

master = MasterProblemAdapter(config, years, base_year, demand, ...)

# Classical MGA
mga = MGAAdapter(master, MGAConfig(
    enabled=True, method="mga",
    num_alternatives=10, slack_fraction=0.05,
))
mga_result = mga.run(use_representative_days=True)

# SPORES with the four canonical objectives
spores = MGAAdapter(master, MGAConfig(
    enabled=True, method="spores",
    objectives=[
        SporesObjective.MIN_TOTAL_BUILD,
        SporesObjective.MAX_TECH_EQUITY,
        SporesObjective.MAX_REGIONAL_EQUITY,
        SporesObjective.EVOLUTIONARY_DIST,
    ],
    slack_fraction=0.05,
))
spores_result = spores.run(use_representative_days=True)

# Same shape; the `objective` key is what differs between alternatives
for r in (mga_result, spores_result):
    print(f"\nMethod: {r['method']}")
    for alt in r["alternatives"]:
        print(f"  Alt {alt['alternative_id']:>2}: "
              f"cost=${alt['cost']/1e9:.2f}B  "
              f"objective={alt['objective']}")
```

See the [MGAAdapter API reference](../api/bridge-adapters.md#mgaadapter) for the full result schema.

---

## When To Use Which

| Situation | Use MGA | Use SPORES |
|-----------|--------:|-----------:|
| Need a large unstructured sample of alternatives (\(K = 20+\)) for statistical robustness | ✅ | ❌ |
| Need *named* alternatives for policy discussion or framework comparison | — | ✅ |
| Want to surface the smallest near-optimal portfolio | — | ✅ (`min_total_build`) |
| Want to surface a spatially-spread plan | — | ✅ (`max_regional_equity`) |
| Want to quantify the technology-substitution envelope | indirect | ✅ (`max_tech_equity`) |
| Want the maximally-different feasible plan vs the cost-optimal | indirect | ✅ (`evolutionary_dist`) |
| Reproducing a Lombardi-2020-style study | — | ✅ |
| First-time exploration of an unfamiliar system | ✅ | — |
| Need both flavours in one run | mix them: `method: spores` with `[hsj_diversity, min_total_build, …]` | |

**Computational cost** is similar: both methods perform one cost-optimal solve plus \(K\) near-optimal solves under the same cost cap, with the JuMP model reused across iterations. Total wall time scales as \((K+1) \cdot T_{\mathrm{master}}\) for either method.

---

## Caveats

- **The cost cap may bind tightly.** Most non-optimal alternatives saturate the slack envelope. This is usually correct but means a 5% slack rarely yields visibly cheaper alternatives — slack measures *how much you let the model spend*, not *how much you actually save*.
- **HSJ tries to be different from previous alternatives.** Run an HSJ loop with `num_alternatives=20` and the late alternatives can be visually similar — the search space within 5% of the optimum is not infinite.
- **`evolutionary_dist` requires the cost-optimal solution as its reference.** ESFEX passes it automatically. If you wrap `apply_spores_objective!` directly, you must hand it `reference_solution=optimal_solution` or you get a runtime error.
- **Battery *energy* (MWh) is excluded from the `min_total_build` and equity objectives.** The mixing of MW and MWh units is intentional: power and energy have different roles in the deployment story and should not compete in the same objective. The energy investment variable is still constrained through the per-tech duration limit.

---

## Case Study: Cuba 11-Alternative SPORES Sweep

To illustrate how each objective shapes the resulting plan, the table below summarises a real SPORES run on the Cuba multi-system test case (two systems, 10 nodes, 25-year horizon, 5% cost slack, four canonical objectives + one HSJ-flavoured alternative seeded inside the same sweep). The cost-optimal seed is `Alt 0`; the other ten alternatives sit at or near the cost cap.

| Alt | Objective | Cost (B USD) | +% vs opt | Total build (GW) | Where the slack went |
|:---:|-----------|----------:|----------:|----------------:|----------------------|
| 0 | `cost_optimal` | 29.74 | — | 14.0 | seed (cost-minimising plan) |
| 1 | `min_total_build` | 30.12 | +1.28% | **12.7** | smallest portfolio — fewer MW, same demand |
| 2 | `hsj_diversity` | 31.23 | +5.00% | 16.5 | structural drift away from the seed |
| 3 | `hsj_diversity` | 31.23 | +5.00% | 16.5 | drifted further from alts 0 + 2 |
| 4 | `hsj_diversity` | 31.23 | +5.00% | 17.0 | continues to push away from prior set |
| 5 | `max_tech_equity` | 31.23 | +5.00% | 16.6 | spreads MW across the technology menu (Solar PV, Wind, Biomass, Battery) |
| 6 | `max_tech_equity` | 31.23 | +5.00% | 17.7 | further tech-spreading (heavier battery) |
| 7 | `max_regional_equity` | 31.23 | +5.00% | 17.2 | invests at Pinar del Rio + Holguín alongside La Habana |
| 8 | `max_regional_equity` | 31.23 | +5.00% | 16.4 | shifts solar from La Habana to outlying nodes |
| 9 | `evolutionary_dist` | 31.23 | +5.00% | 17.9 | **maximally different** from `Alt 0` (heaviest wind + hydroelectric) |
| 10 | `min_total_build` | 30.11 | +1.25% | 12.7 | reaches the same minimum-build basin as `Alt 1` |

Three observations the viewer surfaces directly in the **Cluster Tree** when colouring by objective:

1. **`min_total_build` is the cheapest non-optimal alternative.** It does not saturate the cost cap because the cost-optimal plan is already efficient — there is a regime where you can reduce the build by ~9% with only a ~1.3% cost penalty.
2. **All equity and distance objectives saturate the 5% cap.** Spatial / structural diversity actively trades cost for spread — the cap is what stops them.
3. **`evolutionary_dist` is the natural "opposite plan" to the cost-optimal.** Read alongside `Alt 0` in the **Decision Factors** heatmap, this alternative shows which (tech, node) cells the cost-optimal plan over-uses (red cells in `Alt 0`, blue in `Alt 9`) and which it under-uses (the reverse).

The MGA / SPORES section of the GUI Result Viewer colours every chart by the `objective` tag when the HDF5 reports `method = "spores"`, so this kind of comparison is one click away — no post-processing required.

---

## Further Reading

- [Mathematical formulation](../formulation/capacity-expansion.md#15-mgaspores-near-optimal-alternative-exploration) — full LP statement of every objective with tagged equations
- [SporesObjective enum](../api/config-schema.md#sporesobjective) — the canonical objective list
- [MGAAdapter API](../api/bridge-adapters.md#mgaadapter) — Python entry point
- [`run_spores` Julia entry](../api/julia-api.md#sporesphase-2) — Julia entry point + dispatcher
- [HDF5 schema for `/mga/`](../reference/hdf5-output-schema.md#mgaspores-results) — per-alternative attributes and datasets
- Lombardi F, Pickering B, Colombo E, Pfenninger S. *Policy decision support for renewables deployment through spatially explicit practically optimal alternatives*. **Joule** 2020;4(10):2185–2207. [[bib]](../reference/bibliography.md#ref7)
- DeCarolis JF. *Using modeling to generate alternatives (MGA) to expand our thinking on energy futures*. **Energy Economics** 2011;33(2):145–152. [[bib]](../reference/bibliography.md#ref8)
