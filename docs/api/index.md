# Overview
## Module Map

| Module | Description | Key Exports |
|--------|-------------|-------------|
| [`esfex`](config-loader.md) | Top-level package | `load_config()`, `ESFEXConfig`, `__version__` |
| [`esfex.config.schema`](config-schema.md) | Pydantic configuration models | `ESFEXConfig`, `SystemConfig`, `GeneratorConfig`, `BatteryConfig` |
| [`esfex.config.loader`](config-loader.md) | YAML configuration loading | `load_config()`, `load_yaml()`, `load_system_config()` |
| [`esfex.runner`](runner.md) | Simulation orchestrator | `Orchestrator`, `SimulationState`, `YearResults` |
| [`esfex.bridge.adapters`](bridge-adapters.md) | Julia optimization model wrappers | `PowerSystemAdapter`, `MasterProblemAdapter`, `MGAAdapter` |
| [`esfex.bridge.julia_setup`](bridge-julia-setup.md) | Julia runtime initialization | `initialize_julia()`, `get_julia()`, `get_esfex_module()` |
| [`esfex.io.demand`](io-demand.md) | Demand data loading | `load_demand_data()`, `create_sectoral_demand()`, `DemandDataManager` |
| [`esfex.io.exporter`](io-exporter.md) | Results export (HDF5/CSV/Excel/JSON) | `ResultsExporter`, `export_system_results()` |
| [`esfex.models.ev`](models-ev.md) | EV fleet modeling | `generate_ev_profiles()`, `generate_v2g_availability()`, `aggregate_ev_profiles()` |
| [`esfex.models.ev_adoption`](models-ev-adoption.md) | EV adoption modeling | `run_ev_logistic_adoption()`, `run_ev_bass_diffusion()`, `run_ev_tco_parity()`, `run_ev_policy_driven()` |
| [`esfex.models.ev_analysis`](models-ev-analysis.md) | V2G & grid impact analysis | `generate_charging_profiles()`, `compute_v2g_potential()`, `compute_battery_degradation()`, `assess_grid_impact()` |
| [`esfex.models.solar_rooftop`](models-solar-rooftop.md) | Rooftop solar model | `generate_rooftop_solar_profiles()`, `integrate_rooftop_solar()`, `calculate_rooftop_potential()` |
| [`esfex.models.financial_analysis`](models-financial-analysis.md) | Post-optimization financial analysis | `compute_system_financials()`, `compute_technology_financials()`, `run_sensitivity_analysis()`, `run_monte_carlo()` |
| [`esfex.utils`](utils.md) | Helpers and temporal utilities | `BoundaryConditions`, `calculate_rolling_horizon_windows()` |
| [`esfex.sensitivity`](sensitivity.md) | Sobol sensitivity analysis | `SensitivityEngine`, `SensitivityParameter`, `SobolResult` |

---

## Julia Backend

| Module | Description | Reference |
|--------|-------------|-----------|
| `ESFEX.jl` | Core optimization models | [Julia API](julia-api.md) |
| `power_system.jl` | Operational dispatch (LP/MIP) | [Julia API - Power System](julia-api.md#power-system) |
| `master_problem.jl` | Capacity expansion planning | [Julia API - Master Problem](julia-api.md#master-problem) |
| `transmission_dc.jl` | DC power flow constraints | [Julia API - Transmission DC](julia-api.md#transmission-dc) |
| `mga.jl` | MGA/SPORES near-optimal alternatives | [Julia API - Utility](julia-api.md#utility) |

---

## Architecture Overview

```
YAML Config
    |
    v
load_config() ──> ESFEXConfig (Pydantic validated)
    |
    v
Orchestrator
    |
    ├── MasterProblemAdapter ──> master_problem.jl  (capacity expansion, all years)
    |       |
    |       └── MGAAdapter ──> mga.jl  (near-optimal alternatives)
    |
    └── Per-year loop:
            |
            ├── PowerSystemAdapter ──> power_system.jl  (operational dispatch)
            |       |
            |       └── TransmissionDCAdapter ──> transmission_dc.jl  (DC power flow)
            |
            ├── PrimaryEnergyAdapter ──> primary_energy.jl  (fuel supply chain)
            |
            └── ResultsExporter ──> HDF5 / CSV / Excel / JSON
```

---

## Quick Start

```python
from esfex import load_config
from esfex.runner import Orchestrator

# Load and validate configuration
config = load_config("isla_juventud.yaml")

# Create orchestrator and run simulation
orchestrator = Orchestrator(config, output_dir="./results", config_path="isla_juventud.yaml")
results = orchestrator.run(years=25, start_year=2025)

# Access per-year results
for yr in results:
    print(f"Year {yr.year}: cost=${yr.objective:,.0f}, "
          f"RE={yr.re_penetration:.1%}, "
          f"load shed={yr.load_shed:.1f} MWh")
```

---

## Post-Processing

```python
from esfex.io.exporter import ResultsExporter

exporter = ResultsExporter("results/results_isla_juventud.h5")
exporter.to_csv("results/csv/")
exporter.to_excel("results/report.xlsx")
```

## Financial Analysis

```python
from esfex.models.financial_analysis import (
    FinancialAssumptions,
    compute_system_financials,
    compute_technology_financials,
    run_sensitivity_analysis,
)

assumptions = FinancialAssumptions(discount_rate=0.08, tax_rate=0.25)
financials = compute_system_financials("results/results_isla_juventud.h5", assumptions)

print(f"System NPV:  ${financials.npv_total:,.0f}")
print(f"Project IRR: {financials.project_irr:.1%}")
print(f"System LCOE: ${financials.lcoe_system:.1f}/MWh")
print(f"WACC:        {financials.wacc:.1%}")

# Per-technology breakdown
techs = compute_technology_financials("results/results_isla_juventud.h5", assumptions)
for name, tf in techs.items():
    print(f"  {name}: LCOE=${tf.lcoe:.1f}/MWh, CF={tf.capacity_factor:.1%}")
```

---

## Sensitivity Analysis

```python
from esfex.sensitivity.engine import SensitivityEngine, SensitivityParameter

params = [
    SensitivityParameter(name="Fuel Cost", key="fuel_cost", lower_bound=0.5, upper_bound=3.0),
    SensitivityParameter(name="RE Invest", key="invest_cost_renewables", lower_bound=0.5, upper_bound=2.0),
]
engine = SensitivityEngine(mode="config", parameters=params, n_base_samples=128)
result = engine.run_config_analysis("config.yaml", "output/")
result.to_csv("sobol_indices.csv")
```

---

## Julia API

See the [Julia API](julia-api.md) page for all exported types and functions from `ESFEX.jl`.
