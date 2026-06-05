# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **AC Optimal Power Flow (ACOPF)** formulations for operational dispatch
    - Four formulations: SOC relaxation (W-space), QC relaxation (McCormick envelopes), Polar NLP (exact V-θ), Rectangular NLP (exact e-f)
    - All formulations use Ipopt solver; SDP mode requires MOSEK (not included)
    - Reactive power balance with Q slack variables and configurable penalty
    - Apparent power (MVA) line limits: `P² + Q² ≤ cap²`
    - Reactive generation limits estimated from power factor when Q limits not specified
    - KCL scaling in MW/MVAr for numerical robustness
    - Flat voltage start values for NLP convergence
    - Configurable parameters: voltage limits, power factors, reactance clamp, tap ratio bounds, Q slack penalty
    - Julia: `setup_acopf!()` in `transmission_acopf.jl`, 5 formulation dispatch types
    - Python: `ACPowerFlowConfig` extended with 6 new fields (`load_power_factor`, `q_slack_penalty`, `min_reactance_pu`, `tap_ratio_min`, `tap_ratio_max`, `q_min_ratio`)
    - GUI: Power flow mode selector in System Settings panel with conditional AC parameter visibility
- **Native AC power flow bridge** (`NativeACBridge`) — replaces pandapower for AC power flow and N-1 contingency analysis
    - Uses the Julia Newton-Raphson solver (`transmission_ac.jl`) directly from the GUI, eliminating the pandapower dependency for AC analysis
    - New `GuiACPowerFlowInput` struct and `solve_gui_ac_power_flow()` Julia entry point accept flat arrays (no `PowerSystemInput` required)
    - Drop-in replacement for `PandapowerBridge` via duck-typed interface: `run_power_flow()`, `rerun_power_flow()`, `set_element_in_service()`
    - Pandapower retained only for IEC 60909 short-circuit analysis (`calc_sc()`)
    - Shared result types (`ACPowerFlowResult`, `ShortCircuitResult`) extracted to `ac_types.py` for backend-agnostic consumption
    - GUI automatically prefers native bridge; falls back to pandapower if Julia is not available
- **Enhanced N-1 security analysis** — 10 improvements to contingency analysis
    - PTDF/LODF-based fast contingency evaluation: O(1) per line outage using precomputed sensitivity matrices
    - Performance Index (PI) screening: rank and filter contingencies before detailed analysis
    - Transformer contingencies: treated as branches in DC power flow with impedance-based modeling
    - Battery contingencies: discharging batteries modeled as generation loss with droop redistribution
    - Droop-based generation redistribution: participation factors based on 1/R × P_rated, consistent with frequency analysis
    - N-1-1 (N-k) sequential analysis: second contingency evaluated on the stressed post-first-contingency state
    - Integrated N-1 assessment (`IntegratedN1Analyzer`): combines thermal, frequency, and voltage analysis with composite severity scoring
    - Security-Constrained OPF (SCOPF) in Julia: iterative constraint addition using LODF, adds only binding contingencies
    - Corrective N-1 actions: post-contingency battery and generator response modeled as alternative to preventive reserve
    - N-1 results in HDF5: generation reserve duals, transmission SCOPF duals, binding contingency list, security cost
    - New config fields: `scopf_enabled`, `scopf_max_iterations`, `scopf_violation_tolerance`, `corrective_enabled`
    - New Python modules: `n1_assessment.py` (integrated analyzer), enhanced `contingency.py` (PTDF/LODF, screening, batteries, transformers, N-1-1)
    - New Julia functions: `add_scopf_constraints!()`, `_build_ptdf_matrix()`, `_build_lodf_matrix()`, `_add_corrective_gen_n1!()`
- **Built-in documentation viewer** (Help > Documentation, F1 shortcut)
    - Integrated MkDocs viewer accessible from the GUI menu bar
    - Opens the full documentation site in a docked browser panel
    - F1 keyboard shortcut for quick access from any screen
- **Bidding/offer curves** for generators and batteries
    - Support for flat, linear, stepwise, and exponential cost curves
    - Piecewise-linear (PWL) cost decomposition in operational dispatch and capacity expansion
    - Generator output decomposed into segments with non-decreasing marginal costs (convex)
    - Battery discharge cost curves with same formulation
    - New data structures: `CostCurveBlock` (fraction + price pair), `CostCurveConfig` (curve_type + parameters), `normalize_cost_curve()` (converts any curve to stepwise blocks)
    - New config fields: `fuel_cost_curve` on `GeneratorConfig` and `TechnologyConfig`, `discharge_cost_curve` on `BatteryConfig`
    - GUI widgets for configuring cost curves in generator and battery forms (dropdown with Flat/Linear/Stepwise/Exponential modes)
    - Backward compatible: existing flat `fuel_cost` field continues to work unchanged
- **Per-technology investment model** for capacity expansion
    - `TechnologyConfig`: candidate generation technology with per-node investment costs, limits, fuel type, and availability
    - `BatteryTechnologyConfig`: candidate storage technology with power/energy investment, duration limits, and degradation
    - Master problem optimizes technology-level investments instead of per-generator investments
    - Virtual generators and batteries created from cumulative technology investments for operational dispatch
    - Config sections: `technologies` and `battery_technologies` in YAML
    - Julia structs: `TechnologyConfig`, `BatteryTechnologyConfig` in `types.jl`
- **Plugin system** (QGIS/KiCad-style directory-based discovery)
    - `ESFEXPlugin` base class with lifecycle, runner, Julia, CLI, and GUI hooks
    - `PluginManager` singleton: discovery from `~/.esfex/plugins/`, project-local, `$ESFEX_PLUGIN_PATH`
    - Plugin loading via `importlib.util.spec_from_file_location` (no pip/PyPI required)
    - Enable/disable persistence via `~/.esfex/plugins.json`
    - Install from git (`esfex plugin install --git <url>`) or ZIP (`--zip <path>`)
    - CLI sub-commands: `esfex plugin list|install|uninstall|enable|disable`
    - 7 runner hooks: `pre_simulation`, `post_demand_loaded`, `pre/post_master_problem`, `pre/post_year`, `post_simulation`
    - Julia runtime overlays: plugins can inject `.jl` modules via `include()` without modifying core
    - GUI extensions: tree categories, property forms, toolbar actions, menu items, result variables, map layers, translations
    - GUI **Plugins** menu with "Manage Plugins..." dialog; loaded plugins register their own menu actions
    - `PluginsDialog`: table view of all discovered plugins, install from ZIP/Git, uninstall, enable/disable, open plugins folder
    - `plugins: dict[str, Any]` field on `ESFEXConfig` for per-plugin configuration
    - Safety: all hook calls wrapped in `try/except` --- broken plugins never crash the core
    - Security: ZIP Slip protection, git hooks disabled during clone, URL scheme validation
- **MGA / SPORES near-optimal alternative exploration** — two distinct methods sharing the same cost-slack envelope
    - **Classical MGA** [**[8]**](bibliography.md#ref8) (DeCarolis 2011): Hop-Skip-Jump diversity loop. Generates $K$ alternatives, each maximising a single diversity objective weighted by the frequency score `1 − 2·freq` that penalises previously-selected investment variables. Configuration: `master_problem.mga.method = "mga"`, `num_alternatives`, `slack_fraction`, `investment_threshold`.
    - **SPORES** [**[7]**](bibliography.md#ref7) (Lombardi et al. 2020): one alternative per declared objective. Four canonical objectives ship out of the box: `min_total_build`, `max_tech_equity`, `max_regional_equity`, `evolutionary_dist`. Configuration: `master_problem.mga.method = "spores"`, `objectives = [...]`; the alternative count equals `len(objectives)`.
    - LP-compatible objectives throughout — no binary variables introduced; the L1 norm in `evolutionary_dist` is linearised with positive / negative deviation aux variables.
    - JuMP model reuse: per-objective aux vars / constraints are stashed under `model[:_spores_objective_aux]` and deleted before the next objective is installed, so the model never accumulates dead variables across an SPORES sweep.
    - Julia: `run_mga_spores()` (classical MGA) and `run_spores()` (SPORES sweep) in `mga.jl`, with dispatcher `apply_spores_objective!(model, vars, input, objective; …)`. Per-objective installers: `set_spores_objective!()` (HSJ), `set_min_build_objective!()`, `set_tech_equity_objective!()`, `set_regional_equity_objective!()`, `set_evolutionary_distance_objective!()`.
    - Python: `MGAAdapter` dispatches on `config.method`; the result dict is uniform across both methods, with a per-alternative `objective` tag (`"cost_optimal"`, `"hsj_diversity"`, or a SPORES objective name).
    - Schema: `SporesObjective` enum + cross-field validator on `MGAConfig` that rejects `method='spores'` without objectives and `method='mga'` with objectives populated.
    - GUI: method combo and SPORES-objective checklist in Global Settings; the inactive method's fields hide entirely (rather than just greying out) to keep the form compact.
    - HDF5 export: `/mga.attrs["method"]` and `/mga.attrs["objectives"]` at the group root; per-alternative `attrs["objective"]` on every `/mga/alternative_N/`. Pre-existing fields (`alternative_id`, `is_optimal`, `cost`, `diversity_objective`) preserved.
    - Viewer: `_MGA_OBJECTIVE_COLORS` palette and aware reading in `_build_mga_bundle`. Robust Frontier and Alternative Map colour markers by objective; Cluster Tree's middle annotation ring swaps RE-peak for objective when method is SPORES. Legacy MGA result files render unchanged via back-compat defaults.
- **Battery cyclic SOC constraint**
    - End-of-day SOC must return to initial SOC value: `bat_soc[b, n, hours] == initial_soc`
    - Prevents batteries from acting as infinite energy sources across rolling horizon windows
    - Applied in `add_day_operational_constraints!` in the Julia backend
- **Curtailment limit constraint**
    - Maximum curtailment capped at `max_curtailment_ratio` (default 5%) of total renewable generation
    - Constraint-based approach (not cost-based) to incentivize storage investment
    - Julia: `sum(curtailment) <= ratio * sum(renewable_gen)`
- **Virtual generator/battery concept** for operational dispatch
    - Technology investments from the master problem are materialized as virtual units for hourly dispatch
    - `_build_config_from_cumulative()` creates virtual `GeneratorConfig`/`BatteryConfig` dicts
    - `PowerSystemAdapter._create_input()` creates Julia objects for virtual units from `units_config`
    - Virtual units appear after original units in HDF5 export (name ordering matches adapter)
    - `_rebuild_unit_names()` ensures consistent naming between adapter and exporter
- **Reservoir hydroelectric modeling** as an extension to generators
    - Optional reservoir dynamics: water level tracking, inflows, spillage, evaporation
    - Pumped-storage hydro: pump-back power as demand-side load in power balance
    - Reservoir capacity investment: optimizer can expand reservoir storage
    - Cyclic constraint: end-of-horizon level matches initial (within tolerance)
    - Inflow profile caching for performance (same pattern as availability profiles)
    - Julia: `add_reservoir_constraints!` in `power_system.jl`, investment in `master_problem.jl`
    - Python: 12 fields on `GeneratorConfig`, inflow preloading in `runner.py`
    - GUI: Reservoir section in generator form with conditional visibility
    - HDF5 export: `reservoir_level`, `reservoir_spillage`, `reservoir_pump` datasets
- **Availability profile caching** for performance
    - `_preload_availability_profiles()` loads ALL availability files ONCE at startup
    - `PowerSystemAdapter` receives `availability_cache` parameter
    - `_create_input()` uses cache instead of loading files per window
    - Eliminates thousands of redundant file reads (208 windows x 10 files x 25 years)
- **Keyboard shortcuts dialog** (Help > Keyboard Shortcuts)
    - Displays all available keyboard shortcuts organized by category
    - Includes map navigation, element operations, and menu shortcuts
- **About ESFEX dialog** (Help > About ESFEX)
    - Displays version, build information, and credits
    - Links to documentation and issue tracker
- **Inter-system DC-OPF transmission model** replacing NTC with proper DC power flow
    - Bidirectional flow variable with direction decomposition: `pf = fp - fn`
    - PWL loss approximation for inter-system links with configurable segments
    - Half-loss split on KCL injections at boundary nodes
    - Linear loss fallback when R/X not provided
    - Independent voltage references per system (HVDC-like model)
    - Configuration: `reactance_pu`, `resistance_pu` in `SystemLinkConfig`
    - Configuration: `inter_system_loss_segments` in `MetaNetworkConfig`
- Piecewise linear (PWL) transmission loss model approximating quadratic I^2R losses
- Three loss modes: `none` (lossless), `linear` (legacy constant factor), `pwl` (default)
- Half-loss split formulation for balanced loss allocation at bus endpoints
- Complete documentation site with MkDocs Material
- Mathematical formulation reference for all constraint families
- API reference for all Python and Julia modules
- GUI documentation covering all editor features
- 9 tutorials covering basic to advanced usage
- Configuration reference with all YAML fields
- HDF5 output schema reference
- Constraint catalog with all Julia constraint labels
- Glossary of domain terminology
- Comprehensive documentation expansion across all reference pages
- Availability Generator bundled plugin for generating solar/wind availability profiles from weather reanalysis data (Open-Meteo, NASA POWER, ERA5)
- **Financial Analysis Workflow** — post-optimization financial assessment of energy system investments [48] [64]
    - NPV decomposition, IRR/MIRR, WACC, DSCR, LCOE/LCOS/VALCOE, pro-forma cash flows, and payback analysis
    - Per-technology financial breakdown: capital cost, revenue, capacity factor, LCOE, ROI
    - Sensitivity analysis (one-at-a-time parameter sweeps) with tornado and spider diagrams
    - Monte Carlo simulation with NPV/IRR probability distributions, Value-at-Risk (VaR), and Conditional VaR
    - Python engine (`esfex.models.financial_analysis`) usable independently of the GUI for scripting and batch analysis
    - 8-step GUI wizard accessible via **Workflows > Financial Analysis**
    - Granular cost decomposition from the optimizer (`CostBreakdown` struct with 27 cost components) exported to HDF5 `/cost_breakdown/year_YYYY/`

### Fixed

- **DC power flow self-loop transfer bug** --- transfer variables `trans_(i,i)` created free energy; self-loops now skipped with `i == j && continue` in `master_problem.jl`
- **Free generation bug** ($50 objective) --- generator variables with `rated_power=0` and `invest_max=0` had no upper bound and zero cost; fixed by constraining output to zero when inactive
- **Investments not applied to operational dispatch** --- master problem investments were calculated but not used in operational windows; fixed key parsing (`split("_")[1]` was wrong) and adapter integration
- **Curtailment in DC power flow** --- removed `curtailment` from KCL equation (curtailment is energy never generated, not a power balance term)
- **Penalty coefficient magnitude** --- `fre_penalty` was 600M instead of 600, causing solver stall; now reads from config (`sys.penalties`)
- **Battery acting as infinite generator** --- missing cyclic SOC constraint allowed batteries to generate energy indefinitely; fixed with `bat_soc[b, n, hours] == initial_soc`
- **Virtual generator HDF5 export** --- virtual units (from technology investments) were invisible in HDF5 output; added `_rebuild_unit_names()` to match adapter ordering
- **Availability resolution mismatch** --- operational windows sliced availability as raw hours instead of resolution-adjusted blocks; fixed by passing `ESFEXConfig` instead of `SystemConfig` to adapter

### Changed

- Inter-system transmission now uses DC-OPF formulation instead of NTC-based coupling hack
- Master problem inter-system constraints now model physical power flow with losses
- External injections passed to operational dispatch for proper multi-system coordination

---

## [0.1.3] --- 2026-06-05

### Added

- **Benders decomposition** as an optional master-problem solver
  (`master_problem.solver_method: monolithic | benders`). The investment-only
  master with `θ[y]` recourse variables plus per-representative-day dispatch
  subproblems and optimality cuts is beneficial for very large problems.
  Configurable via `benders_max_iterations`, `benders_tolerance`,
  `benders_lol_penalty_cap`, and selectable from the Studio's master-problem
  settings. Monolithic remains the default.
- **OpenSSF Best Practices** badge.

### Fixed

- **Grid Builder bus-distribution step no longer freezes the UI** on
  whole-country footprint sets: building-footprint classification and the
  nearest-bus assignment run in a background thread, with a vectorised
  classifier, single centroid pass, and `np.bincount` accumulation.

---

## [0.1.1] --- 2026-06-04

### Fixed

- **Grid Builder demand forecast crash**: the per-node demand forecast read
  `latitude`/`longitude` on grid nodes, but `GuiNode` exposes its geographic
  position as `centroid_lat`/`centroid_lng`. Running the forecast raised
  `AttributeError: 'GuiNode' object has no attribute 'latitude'`.
- **Fuel-entry-point duplication crash**: duplicating a fuel entry point nudged
  `coordinate.latitude`/`.longitude`, but `GeoPoint` uses `lat`/`lng`, so the
  action raised an `AttributeError`.
- **GeoJSON fuel-entry import**: fuel entry points were constructed with invalid
  `max_import_rate`/`import_cost` keyword arguments; these are now passed through
  the `fuel_params` mapping.
- **GeoJSON node import**: nodes are now created from `Point` features and snapped
  to the nearest existing node by great-circle (haversine) distance to the
  centroid, instead of always attaching to the first node.

### Changed

- Native Julia test suite expanded from a handful of smoke tests to full
  unit and end-to-end model-solve coverage; Julia core coverage is now reported
  to Codecov under a dedicated `julia` flag.

---

## [0.1.0] --- 2025-XX-XX

### Added

- **Core Simulation Engine**
    - Capacity expansion planning (Master Problem) with NPV minimization
    - Operational dispatch with rolling horizon (48h windows, 6h overlap)
    - Economic dispatch (LP) and unit commitment (MIP) modes
    - Age-based retirement: existing and invested units
    - Multi-year simulation with demand growth

- **Power System Modeling**
    - Generator constraints: capacity, ramp rates, min up/down time, efficiency curves
    - Battery constraints: SOC dynamics, charge/discharge limits, cyclic SOC, spillage
    - DC power flow with Kirchhoff's laws (KCL/KVL)
    - Transmission line capacity constraints with investment
    - Curtailment limits (5% of RE generation by default)
    - RE penetration targets with annual progression
    - CO2 budget constraints
    - System inertia requirements
    - N-1 security (transmission and generation contingencies)
    - Sectoral demand with criticality-based load shedding
    - Demand flexibility and shifting

- **Advanced Features**
    - EV fleet modeling with S-curve adoption and V2G
    - Rooftop solar with adoption scenarios
    - Primary energy supply chain (import, storage, transport)
    - Electrolyzers (PEM, Alkaline, SOE)
    - Multi-system coordination with inter-system links
    - Stochastic capacity expansion with scenario multipliers
    - Sobol sensitivity analysis (via SALib)

- **Network Equipment**
    - AC/DC converters (VSC and LCC)
    - Frequency converters
    - Transformers with impedance modeling
    - Bus-level modeling with voltage/frequency attributes
    - Development zones for technology deployment

- **Results & Export**
    - HDF5 output with full time series
    - Derived metrics: LCOE, VALLCOE, capacity factor
    - Electricity price decomposition (energy, congestion)
    - Technology selling prices and revenue analysis
    - CSV, Excel, and JSON export formats

- **GUI Editor**
    - PySide6 + Leaflet.js interactive map editor
    - Element tree with 19 categories per system
    - Context-sensitive property forms for all element types
    - Polyline trace for transmission lines and fuel routes
    - Magnetic snapping for equipment placement
    - Multi-system management
    - Inter-system link editor
    - GeoJSON import with feature-to-element mapping
    - Light, Dark, and Twilight themes
    - Python console and script editor
    - Analysis wizards: Solar PV, Wind, OTEC, Rooftop Solar

- **Configuration**
    - Pydantic v2 schema validation
    - YAML configuration format
    - CLI with run, validate, export, editor, info commands

- **Solver Support**
    - HiGHS (default, open-source)
    - Gurobi (commercial, optional)
    - CPLEX (commercial, optional)
    - CBC (open-source, optional)
    - GLPK (open-source, optional)

- **Python-Julia Bridge**
    - juliacall integration for seamless optimization calls
    - System image support for faster startup
    - Availability profile caching for performance
