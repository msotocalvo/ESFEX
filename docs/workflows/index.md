# Overview
Multi-step analysis wizards accessible from the **Workflows** menu. Each wizard provides step-by-step guidance, progress tracking, configurable parameters, and map-based visualization where applicable.

The resource assessment wizards (Solar PV, Wind, OTEC, Rooftop Solar) and the EV & V2G wizard are organized in two phases:

- **Phase A**: Resource or fleet assessment — defines the study domain, configures the technology baseline, runs the analysis, and presents results.
- **Phase B**: Advanced analysis and grid integration — detailed characterization, financial analysis, scenario comparison, and generation of configuration parameters for the optimization model.

The Demand Distribution and Grid Auto-Mapping wizards operate in a single phase.

Resource computations are powered by standalone Python libraries that can also be used independently for scripting and batch analysis:

| Library | Scope | PyPI |
|---------|-------|------|
| [**solarex**](https://pypi.org/project/solarex/) | Ground-mounted solar PV resource assessment and MCDA | `pip install solarex` |
| [**windrex**](https://pypi.org/project/windrex/) | Wind resource assessment and wake modeling | `pip install windrex` |
| [**rooftex**](https://pypi.org/project/rooftex/) | Rooftop solar potential and building footprint analysis | `pip install rooftex` |
| [**evrex**](https://pypi.org/project/evrex/) | EV fleet adoption, charging demand, and V2G analysis | `pip install evrex` |
| [**otex**](https://pypi.org/project/otex/) | Ocean thermal energy conversion (OTEC) assessment | `pip install otex` |


---


## Workflow Catalog

| Workflow | Steps | Description | Library |
|----------|-------|-------------|---------|
| [Solar PV Assessment](solar-pv.md) | 9 (5+4) | Ground-mounted PV potential via Multi-Criteria Decision Analysis (MCDA), GHI characterization, financial analysis, array layout optimization, and hourly availability profile generation | solarex |
| [Wind Assessment](wind.md) | 9 (5+4) | Wind resource assessment with Weibull fitting, MCDA site ranking, financial analysis, Jensen/Park wake modeling, and hourly availability profile generation | windrex |
| [OTEC Assessment](otec.md) | 11 (4+7) | Ocean Thermal Energy Conversion site assessment with thermodynamic cycle modeling, cold water pipe sizing, Monte Carlo uncertainty quantification, and Sobol sensitivity analysis | otex |
| [Rooftop Solar](rooftop-solar.md) | 9 (5+4) | Distributed rooftop PV potential from building footprints, adoption curve modeling (logistic, Gompertz, Bass, agent-based), and scenario-based grid integration | rooftex |
| [Demand Distribution](demand-distribution.md) | 5 | Spatial distribution of nodal demand among busbars using building footprint classification and density-based or centroid-based clustering | — |
| [Grid Auto-Mapping](grid-auto-mapping.md) | 5 | Automated power grid construction from open geospatial databases (OpenStreetMap, WRI, GEM, GridFinder) with node placement via clustering and fuel routing | — |
| [EV & V2G Assessment](ev-v2g.md) | 9 (5+4) | Electric vehicle fleet adoption modeling, charging demand scenario generation, vehicle-to-grid potential assessment, battery degradation analysis, and grid impact evaluation | evrex |
| [Financial Analysis](financial-analysis.md) | 8 (4+4) | Post-optimization financial assessment: NPV decomposition, IRR/MIRR, LCOE/VALCOE, pro-forma cash flows, sensitivity analysis, and Monte Carlo risk simulation | — |
