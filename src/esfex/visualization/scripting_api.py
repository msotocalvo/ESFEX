"""Scripting API for the ESFEX Python console.

Provides a ``esfex`` namespace object with high-level wrappers around
the optimization runner, result loading, sensitivity analysis, and
plotting utilities.  Injected into the console at startup so users can
write scripts like::

    cfg = esfex.load_config("my_system.yaml")
    results = esfex.run(cfg, output_dir="results/")
    esfex.plot.generation_mix(results, 2025)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Union


# ── Plot sub-namespace ────────────────────────────────────────────


class _PlotNamespace:
    """Chart helpers accessible via ``esfex.plot.*``."""

    @staticmethod
    def generation_mix(results: dict, year: int):
        """Stacked area chart of generation by source for *year*.

        Parameters
        ----------
        results : dict
            Output of ``esfex.load_results()``.
        year : int
            Simulation year to plot.

        Returns
        -------
        matplotlib.figure.Figure
        """
        import matplotlib.pyplot as plt
        import numpy as np

        yr_data = results.get("generation", {}).get(year)
        if yr_data is None:
            print(f"No generation data for year {year}.")
            return None
        gen_names = results.get("generator_names", [])
        hours = np.arange(yr_data.shape[-1])

        fig, ax = plt.subplots(figsize=(12, 5))
        if yr_data.ndim == 3:
            # [gen x node x hour] → aggregate nodes
            data = yr_data.sum(axis=1)
        else:
            data = yr_data

        ax.stackplot(hours, data, labels=gen_names[:len(data)])
        ax.set_xlabel("Hour")
        ax.set_ylabel("MW")
        ax.set_title(f"Generation Mix — {year}")
        ax.legend(loc="upper left", fontsize=7, ncol=3)
        fig.tight_layout()
        plt.show()
        return fig

    @staticmethod
    def prices(results: dict, year: int):
        """Nodal price time series for *year*.

        Returns
        -------
        matplotlib.figure.Figure
        """
        import matplotlib.pyplot as plt
        import numpy as np

        yr_data = results.get("prices", {}).get(year)
        if yr_data is None:
            print(f"No price data for year {year}.")
            return None

        fig, ax = plt.subplots(figsize=(12, 4))
        if yr_data.ndim == 2:
            for n in range(yr_data.shape[0]):
                ax.plot(yr_data[n], label=f"Node {n}", alpha=0.7, linewidth=0.5)
        else:
            ax.plot(yr_data)
        ax.set_xlabel("Hour")
        ax.set_ylabel("$/MWh")
        ax.set_title(f"Nodal Prices — {year}")
        if yr_data.ndim == 2 and yr_data.shape[0] <= 10:
            ax.legend(fontsize=7)
        fig.tight_layout()
        plt.show()
        return fig

    @staticmethod
    def investments(results: dict):
        """Bar chart of cumulative investment decisions across years.

        Returns
        -------
        matplotlib.figure.Figure
        """
        import matplotlib.pyplot as plt

        inv = results.get("investments", {})
        if not inv:
            print("No investment data found.")
            return None

        fig, ax = plt.subplots(figsize=(10, 5))
        years = sorted(inv.keys())
        tech_names: set[str] = set()
        for yr in years:
            tech_names.update(inv[yr].keys())
        tech_names_sorted = sorted(tech_names)

        bottom = [0.0] * len(years)
        for tech in tech_names_sorted:
            vals = [inv[yr].get(tech, 0.0) for yr in years]
            ax.bar(years, vals, bottom=bottom, label=tech)
            bottom = [b + v for b, v in zip(bottom, vals)]

        ax.set_xlabel("Year")
        ax.set_ylabel("MW")
        ax.set_title("Investment Decisions")
        ax.legend(fontsize=7, ncol=2)
        fig.tight_layout()
        plt.show()
        return fig

    @staticmethod
    def load_duration(results: dict, year: int):
        """Load duration curve for *year*.

        Returns
        -------
        matplotlib.figure.Figure
        """
        import matplotlib.pyplot as plt
        import numpy as np

        demand = results.get("demand", {}).get(year)
        if demand is None:
            print(f"No demand data for year {year}.")
            return None

        fig, ax = plt.subplots(figsize=(10, 4))
        if demand.ndim == 2:
            total = demand.sum(axis=0)
        else:
            total = demand
        sorted_load = np.sort(total)[::-1]
        ax.plot(sorted_load)
        ax.set_xlabel("Hours")
        ax.set_ylabel("MW")
        ax.set_title(f"Load Duration Curve — {year}")
        ax.fill_between(range(len(sorted_load)), sorted_load, alpha=0.3)
        fig.tight_layout()
        plt.show()
        return fig


# ── Workflows sub-namespace ────────────────────────────────────────


class _WorkflowsNamespace:
    """Standalone analysis workflows accessible via ``esfex.workflows.*``.

    Each method wraps a pure-Python model module from ``esfex.models``
    with no GUI dependencies.
    """

    # ── Wind ──────────────────────────────────────────────────

    @staticmethod
    def wind_weibull(speeds):
        """Fit Weibull distribution to wind speed data.

        Parameters
        ----------
        speeds : array-like
            Wind speed time series (m/s).

        Returns
        -------
        tuple (k, A)
            Shape and scale parameters.

        Example
        -------
        >>> k, A = esfex.workflows.wind_weibull(wind_speeds)
        >>> print(f"Weibull k={k:.2f}, A={A:.2f} m/s")
        """
        from esfex.models.wind_models import fit_weibull
        import numpy as np
        return fit_weibull(np.asarray(speeds))

    @staticmethod
    def wind_financials(capacity_mw=10.0, capacity_factor=0.30,
                        capex_per_kw=1500.0, opex_per_kw_yr=30.0,
                        discount_rate=0.08, lifetime_years=25):
        """Compute wind project financials (LCOE, NPV, IRR).

        Returns
        -------
        WindFinancialResults
            Object with ``.lcoe``, ``.npv``, ``.irr`` attributes.
        """
        from esfex.models.wind_models import (
            WindFinancialInputs, compute_wind_financials,
        )
        inputs = WindFinancialInputs(
            capacity_mw=capacity_mw, capacity_factor=capacity_factor,
            capex_per_kw=capex_per_kw, opex_per_kw_yr=opex_per_kw_yr,
            discount_rate=discount_rate, lifetime_years=lifetime_years,
        )
        return compute_wind_financials(inputs)

    # ── Solar PV ──────────────────────────────────────────────

    @staticmethod
    def solar_financials(capacity_mw=10.0, capacity_factor=0.20,
                         capex_per_kw=1000.0, opex_per_kw_yr=15.0,
                         discount_rate=0.08, lifetime_years=25,
                         electricity_price=50.0, degradation_rate=0.005):
        """Compute solar PV project financials (LCOE, NPV, IRR).

        Returns
        -------
        SolarFinancialResults
            Object with ``.lcoe``, ``.npv``, ``.irr``, ``.cash_flows``.
        """
        from esfex.models.solar_pv_models import (
            SolarFinancialInputs, compute_pv_financials,
        )
        inputs = SolarFinancialInputs(
            capacity_mw=capacity_mw, capacity_factor=capacity_factor,
            capex_per_kw=capex_per_kw, opex_per_kw_yr=opex_per_kw_yr,
            discount_rate=discount_rate, lifetime_years=lifetime_years,
            electricity_price=electricity_price,
            degradation_rate=degradation_rate,
        )
        return compute_pv_financials(inputs)

    @staticmethod
    def solar_peak_sun_hours(ghi_hourly):
        """Peak sun hours from hourly GHI (W/m²) array."""
        from esfex.models.solar_pv_models import compute_peak_sun_hours
        import numpy as np
        return compute_peak_sun_hours(np.asarray(ghi_hourly))

    # ── Financial Analysis (post-optimization) ────────────────

    @staticmethod
    def system_financials(h5_path, assumptions=None):
        """Full financial analysis from HDF5 results.

        Parameters
        ----------
        h5_path : str or Path
            Path to simulation results HDF5 file.
        assumptions : FinancialAssumptions, optional
            Override default financial parameters.

        Returns
        -------
        SystemFinancials
            NPV, IRR, LCOE, DSCR, cash flows, tech-level breakdown.

        Example
        -------
        >>> fin = esfex.workflows.system_financials("results/system.h5")
        >>> print(f"System LCOE: {fin.lcoe_system:.2f} $/MWh")
        >>> print(f"Project IRR: {fin.project_irr:.1%}")
        """
        from esfex.models.financial_analysis import compute_system_financials
        return compute_system_financials(h5_path, assumptions)

    @staticmethod
    def financial_monte_carlo(h5_path, n_simulations=1000, seed=42,
                              assumptions=None):
        """Monte Carlo financial risk analysis.

        Returns
        -------
        MonteCarloResult
            Distributions of NPV, IRR, LCOE across simulations.
        """
        from esfex.models.financial_analysis import (
            run_monte_carlo, FinancialAssumptions,
        )
        return run_monte_carlo(
            h5_path, assumptions or FinancialAssumptions(),
            n_simulations=n_simulations, seed=seed,
        )

    # ── Hazard Assessment ─────────────────────────────────────

    @staticmethod
    def hazard_screening(coordinates):
        """Screen multiple locations for all 7 natural hazard types.

        Parameters
        ----------
        coordinates : list of (lat, lon) tuples
            Locations to screen.

        Returns
        -------
        HazardIntensityMap
            Categorical risk levels per node per hazard.

        Example
        -------
        >>> coords = [(21.5, -77.8), (22.4, -79.9)]
        >>> him = esfex.workflows.hazard_screening(coords)
        """
        from esfex.models.hazard_assessment import ScreeningFetcher
        fetcher = ScreeningFetcher()
        return fetcher.fetch(coordinates)

    @staticmethod
    def risk_assessment(hazard_maps, node_components,
                        combination="independent",
                        component_values=None):
        """Multi-hazard composite risk assessment.

        Parameters
        ----------
        hazard_maps : list[HazardIntensityMap]
            From ``hazard_screening`` or per-hazard fetchers.
        node_components : dict
            ``{node_idx: ["solar_pv", "battery", ...]}``.
        combination : str
            ``"independent"``, ``"copula"``, or ``"mcda"``.

        Returns
        -------
        list[NodeRiskProfile]
            Per-node risk profiles with failure probs and EAL.
        """
        from esfex.models.hazard_assessment import (
            CompositeRiskAssessment, FragilityLibrary,
        )
        lib = FragilityLibrary()
        assessment = CompositeRiskAssessment(
            fragility_library=lib, combination_method=combination,
        )
        return assessment.assess(
            hazard_maps, node_components,
            component_values=component_values,
        )

    # ── EV Profiles ───────────────────────────────────────────

    @staticmethod
    def ev_profiles(num_nodes, num_hours, ev_categories, ev_quantity,
                    base_patterns, base_year=2025, target_year=2050,
                    growth_rate=0.12):
        """Generate EV charging demand profiles with S-curve growth.

        Returns
        -------
        DataFrame
            Hourly EV demand per node/category (MW).

        Example
        -------
        >>> profiles = esfex.workflows.ev_profiles(
        ...     num_nodes=3, num_hours=8760,
        ...     ev_categories={"light": {"charging_power": 7.0}},
        ...     ev_quantity={"light": [100, 200, 150]},
        ...     base_patterns={"light": [0]*6 + [0.3]*4 + [0.8]*4 + [1.0]*4 + [0.5]*6},
        ... )
        """
        from esfex.models.ev import generate_ev_profiles
        return generate_ev_profiles(
            num_nodes, num_hours, ev_categories, ev_quantity,
            base_patterns, base_year=base_year, target_year=target_year,
            growth_rate=growth_rate,
        )

    # ── Demand Estimation ─────────────────────────────────────

    @staticmethod
    def estimate_demand(gdp_per_capita, population, urbanization_pct,
                        temperatures_hourly, latitude, longitude,
                        base_temp=21.0, beta_cdd=0.0, beta_hdd=0.0):
        """Estimate hourly demand using the ML+econometric model.

        Requires ``pip install esfex[ml]`` (xgboost).

        Parameters
        ----------
        gdp_per_capita : float
            USD per capita.
        population : int
            Total population.
        urbanization_pct : float
            0-100.
        temperatures_hourly : array
            Hourly temperatures (°C), length = n_days × 24.
        latitude, longitude : float
            Site coordinates.

        Returns
        -------
        np.ndarray
            Hourly demand estimate (MW), same length as temperatures.
        """
        import numpy as np
        from esfex.models.demand_ml import (
            DemandMLModel, build_inference_features, harmonic_reconstruct,
            aggregate_to_3h, compute_hdd_cdd_3h,
        )

        if not DemandMLModel.is_available():
            raise RuntimeError(
                "Demand ML model not available. Install with: "
                "pip install esfex[ml]"
            )

        model = DemandMLModel.load_bundled()
        temps = np.asarray(temperatures_hourly)
        n_days = len(temps) // 24
        temps_daily = temps[:n_days * 24].reshape(n_days, 24)
        temps_3h = aggregate_to_3h(temps_daily)
        hdd_3h, cdd_3h = compute_hdd_cdd_3h(temps_3h, base_temp)

        # Predict 3-hourly shape factors
        demand_3h = np.zeros((n_days, 8))
        for d in range(n_days):
            for h in range(8):
                features = build_inference_features(
                    gdp_per_capita, population, urbanization_pct,
                    100.0,  # electricity_access
                    temps_3h[d, h], hdd_3h[d, h], cdd_3h[d, h],
                    h * 3, (d % 365) // 30 + 1, d % 7,
                    latitude, longitude,
                )
                demand_3h[d, h] = model.predict(features)[0]

        # Reconstruct hourly
        hourly = harmonic_reconstruct(
            demand_3h, temps_daily, beta_cdd, beta_hdd, base_temp,
        )
        return hourly.ravel()

    # ── Climate Profiles ──────────────────────────────────────

    @staticmethod
    def climate_demand_adjustment(base_demand, temperatures, base_temp=24.0,
                                  alpha_cool=2.5, alpha_heat=0.5):
        """Adjust demand for climate change (HDD/CDD scaling).

        Parameters
        ----------
        base_demand : array
            Baseline hourly demand (MW).
        temperatures : array
            Hourly temperatures (°C).
        base_temp : float
            Reference temperature.
        alpha_cool, alpha_heat : float
            %/°C sensitivity coefficients.

        Returns
        -------
        np.ndarray
            Adjusted demand.
        """
        from esfex.models.climate_profiles import compute_climate_demand
        import numpy as np
        return compute_climate_demand(
            np.asarray(base_demand), np.asarray(temperatures),
            base_temp=base_temp, alpha_cool=alpha_cool,
            alpha_heat=alpha_heat,
        )

    # ── OTEC ──────────────────────────────────────────────────

    @staticmethod
    def otec_capacity_factors(t_warm, t_cold, gross_power_kw=10000.0):
        """Compute daily OTEC capacity factors from temperature profiles.

        Parameters
        ----------
        t_warm, t_cold : array
            Daily warm/cold water temperatures (°C).
        gross_power_kw : float
            Gross plant power.

        Returns
        -------
        np.ndarray
            Daily capacity factors (0-1).
        """
        from esfex.models.otec_models import DailyOTECData, compute_daily_cf
        import numpy as np
        data = DailyOTECData(
            timestamps=[f"day_{i}" for i in range(len(t_warm))],
            t_warm=np.asarray(t_warm),
            t_cold=np.asarray(t_cold),
        )
        return compute_daily_cf(data, gross_power_kw)

    # ── TSAM (Time Series Aggregation) ────────────────────────

    @staticmethod
    def tsam_cluster(demand, n_periods=10, method="kmedoids"):
        """Cluster demand into representative periods.

        Parameters
        ----------
        demand : array (n_hours,) or (n_days, 24)
            Demand time series.
        n_periods : int
            Number of representative periods.
        method : str
            ``"kmedoids"`` or ``"kmeans"``.

        Returns
        -------
        TSAMResult
            ``.representative_days``, ``.weights``, ``.mapping``.
        """
        from esfex.models.tsam import compute_tsam_periods
        import numpy as np
        return compute_tsam_periods(
            np.asarray(demand), n_periods=n_periods, method=method,
        )

    @staticmethod
    def help():
        """Print workflows reference."""
        text = """
── Workflows API ──────────────────────────────────────────────

  Wind:
    esfex.workflows.wind_weibull(speeds)          → (k, A)
    esfex.workflows.wind_financials(...)           → LCOE, NPV, IRR

  Solar PV:
    esfex.workflows.solar_financials(...)          → LCOE, NPV, IRR
    esfex.workflows.solar_peak_sun_hours(ghi)      → float (hours)

  Financial (post-optimization):
    esfex.workflows.system_financials(h5_path)     → full breakdown
    esfex.workflows.financial_monte_carlo(h5_path) → risk distributions

  Hazard & Risk:
    esfex.workflows.hazard_screening(coords)       → HazardIntensityMap
    esfex.workflows.risk_assessment(maps, comps)   → NodeRiskProfiles

  EV:
    esfex.workflows.ev_profiles(...)               → DataFrame (MW/h)

  Demand:
    esfex.workflows.estimate_demand(...)            → hourly MW array
    esfex.workflows.climate_demand_adjustment(...)  → adjusted MW array

  OTEC:
    esfex.workflows.otec_capacity_factors(tw, tc)  → daily CFs

  Time Series:
    esfex.workflows.tsam_cluster(demand, n)        → representative periods
"""
        print(text)


# ── Main API class ────────────────────────────────────────────────


class ESFEXAPI:
    """High-level scripting API for the ESFEX console.

    Access via the ``esfex`` object in the console::

        esfex.help()
        cfg = esfex.load_config("config.yaml")
        results = esfex.run(cfg, "output/")
    """

    plot = _PlotNamespace()
    workflows = _WorkflowsNamespace()

    # ── Properties ────────────────────────────────────────────

    @property
    def version(self) -> str:
        """Return the esfex version string."""
        try:
            from esfex import __version__
            return __version__
        except ImportError:
            return "unknown"

    @property
    def available_solvers(self) -> list[str]:
        """List optimization solvers that are installed and usable."""
        solvers = []
        for name, mod in [
            ("HiGHS", "highspy"),
            ("Gurobi", "gurobipy"),
            ("CPLEX", "cplex"),
            ("SCIP", "pyscipopt"),
            ("CBC", "cylp"),
            ("GLPK", "glpk"),
        ]:
            try:
                __import__(mod)
                solvers.append(name)
            except ImportError:
                pass
        return solvers

    # ── Config ────────────────────────────────────────────────

    @staticmethod
    def load_config(path: Union[str, Path]):
        """Load a YAML configuration file.

        Parameters
        ----------
        path : str or Path
            Path to the YAML config file.

        Returns
        -------
        ESFEXConfig
            Validated configuration object.  All fields are accessible
            as attributes, e.g. ``cfg.systems``, ``cfg.temporal``.

        Example
        -------
        >>> cfg = esfex.load_config("isla_juventud.yaml")
        >>> print(cfg.temporal.num_years)
        """
        from esfex.config.loader import load_config
        return load_config(Path(path))

    # ── Results I/O ───────────────────────────────────────────

    @staticmethod
    def load_results(path: Union[str, Path]) -> dict[str, Any]:
        """Load simulation results from an HDF5 file.

        Returns a dict with numpy arrays organized by category and year::

            {
                "years": [2025, 2026, ...],
                "generation": {2025: ndarray, ...},
                "demand": {2025: ndarray, ...},
                "prices": {2025: ndarray, ...},
                "storage": {2025: ndarray, ...},
                "power_flow": {2025: ndarray, ...},
                "curtailment": {2025: ndarray, ...},
                "investments": {2025: {"Solar_PV": 100.0, ...}, ...},
                "summary": {2025: {"objective": ..., "re_penetration": ...}},
                "generator_names": [...],
                "battery_names": [...],
            }

        Example
        -------
        >>> r = esfex.load_results("results/system_Cuba.h5")
        >>> print(r["years"])
        >>> gen = r["generation"][2025]  # numpy array
        """
        import h5py
        import numpy as np

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Results file not found: {path}")

        out: dict[str, Any] = {
            "years": [],
            "generation": {},
            "demand": {},
            "prices": {},
            "storage": {},
            "power_flow": {},
            "curtailment": {},
            "investments": {},
            "summary": {},
            "generator_names": [],
            "battery_names": [],
        }

        with h5py.File(path, "r") as f:
            # Extract years from detailed_results groups
            for key in sorted(f.keys()):
                if key.startswith("detailed_results"):
                    # Nested: detailed_results/year_XXXX_threshold_Y/
                    for sub in sorted(f[key].keys()):
                        parts = sub.split("_")
                        for i, p in enumerate(parts):
                            if p == "year" and i + 1 < len(parts):
                                try:
                                    yr = int(parts[i + 1])
                                    if yr not in out["years"]:
                                        out["years"].append(yr)
                                    grp = f[key][sub]
                                    # Load available datasets
                                    for ds_name, out_key in [
                                        ("generation", "generation"),
                                        ("demand", "demand"),
                                        ("prices", "prices"),
                                        ("storage", "storage"),
                                        ("power_flow", "power_flow"),
                                        ("curtailment", "curtailment"),
                                    ]:
                                        if ds_name in grp:
                                            out[out_key][yr] = np.array(grp[ds_name])
                                except (ValueError, IndexError):
                                    pass

            # Summary results
            if "summary_results" in f:
                for key in f["summary_results"]:
                    try:
                        yr = int(key.replace("year_", ""))
                        grp = f["summary_results"][key]
                        summary = {}
                        for attr in grp.attrs:
                            summary[attr] = grp.attrs[attr]
                        for ds in grp:
                            summary[ds] = np.array(grp[ds])
                        out["summary"][yr] = summary
                    except (ValueError, KeyError):
                        pass

            # Generator/battery names from system_configuration
            if "system_configuration" in f:
                sc = f["system_configuration"]
                if "generator_names" in sc:
                    out["generator_names"] = list(sc["generator_names"].asstr()[:])
                if "battery_names" in sc:
                    out["battery_names"] = list(sc["battery_names"].asstr()[:])

        out["years"].sort()
        n_years = len(out["years"])
        n_gen = sum(len(v) for v in out["generation"].values())
        print(f"Loaded {path.name}: {n_years} years, "
              f"{len(out['generator_names'])} generators, "
              f"{len(out['battery_names'])} batteries")
        return out

    @staticmethod
    def export_results(
        h5_path: Union[str, Path],
        output_dir: Union[str, Path],
        fmt: str = "csv",
    ) -> None:
        """Export HDF5 results to CSV, Excel, or JSON.

        Parameters
        ----------
        h5_path : str or Path
            Path to the HDF5 results file.
        output_dir : str or Path
            Directory to write exported files.
        fmt : str
            Format: ``"csv"``, ``"excel"``, or ``"json"``.

        Example
        -------
        >>> esfex.export_results("results/system.h5", "exports/", "csv")
        """
        from esfex.io.exporter import ResultsExporter

        exporter = ResultsExporter(Path(h5_path))
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if fmt == "csv":
            exporter.to_csv(output_dir)
        elif fmt == "excel":
            exporter.to_excel(output_dir / "results.xlsx")
        elif fmt == "json":
            exporter.to_json(output_dir / "results.json")
        else:
            raise ValueError(f"Unknown format: {fmt!r}. Use 'csv', 'excel', or 'json'.")
        print(f"Exported to {output_dir} ({fmt})")

    # ── Simulation ────────────────────────────────────────────

    @staticmethod
    def run(
        config: Any,
        output_dir: Union[str, Path] = "results",
        years: int | None = None,
        start_year: int = 2025,
    ):
        """Run a full ESFEX simulation (master + operational dispatch).

        Parameters
        ----------
        config : ESFEXConfig or str or Path
            Configuration object or path to YAML file.
        output_dir : str or Path
            Directory for output HDF5 files.
        years : int, optional
            Number of years to simulate (default: all from config).
        start_year : int
            First simulation year.

        Returns
        -------
        list[YearResults]
            One result object per simulated year.

        Example
        -------
        >>> cfg = esfex.load_config("config.yaml")
        >>> results = esfex.run(cfg, "output/", years=5)
        >>> for yr in results:
        ...     print(f"{yr.year}: obj={yr.objective:.0f}, RE={yr.re_penetration:.1%}")
        """
        from esfex.config.loader import load_config
        from esfex.runner import Orchestrator

        if isinstance(config, (str, Path)):
            config_path = Path(config)
            config = load_config(config_path)
        else:
            config_path = None

        orch = Orchestrator(
            config, output_dir=Path(output_dir), config_path=config_path,
        )
        return orch.run(years=years, start_year=start_year)

    # ── Individual solvers ────────────────────────────────────

    @staticmethod
    def solve_master(config: Any, solver: str = "highs") -> dict[str, Any]:
        """Solve only the master (capacity expansion) problem.

        Parameters
        ----------
        config : ESFEXConfig or str or Path
            Configuration.
        solver : str
            Solver name (default: "highs").

        Returns
        -------
        dict
            Keys: ``investments``, ``retirements``, ``re_targets``,
            ``objective``, ``solve_time``.

        Example
        -------
        >>> sol = esfex.solve_master("config.yaml")
        >>> print(sol["investments"])
        """
        from esfex.bridge.adapters import MasterProblemAdapter
        from esfex.config.loader import load_config

        if isinstance(config, (str, Path)):
            config = load_config(Path(config))

        adapter = MasterProblemAdapter(config, solver_name=solver)
        adapter.build_model()
        status = adapter.solve()

        return {
            "status": status,
            "objective": adapter.get_objective_value(),
            "investments": adapter.get_investment_decisions(),
            "retirements": adapter.get_retirement_decisions(),
            "re_targets": adapter.get_re_targets(),
            "solution": adapter.solution,
        }

    @staticmethod
    def solve_dispatch(
        config: Any,
        solver: str = "highs",
    ) -> dict[str, Any]:
        """Solve a single operational dispatch problem.

        Parameters
        ----------
        config : ESFEXConfig or SystemConfig or str or Path
            Configuration.
        solver : str
            Solver name.

        Returns
        -------
        dict
            Solution values: gen_output, prices, curtailment, etc.

        Example
        -------
        >>> sol = esfex.solve_dispatch("config.yaml")
        >>> print(sol.keys())
        """
        from esfex.bridge.adapters import PowerSystemAdapter
        from esfex.config.loader import load_config

        if isinstance(config, (str, Path)):
            config = load_config(Path(config))

        adapter = PowerSystemAdapter(config, solver_name=solver)
        adapter.build_model()
        status = adapter.solve()

        return {
            "status": status,
            "objective": adapter.get_objective_value(),
            **adapter.get_solution_values(),
        }

    # ── Sensitivity ───────────────────────────────────────────

    @staticmethod
    def sensitivity(
        config_path: Union[str, Path],
        parameters: list[dict[str, Any]] | None = None,
        n_samples: int = 64,
        output_dir: Union[str, Path] = "sensitivity_output",
    ):
        """Run Sobol global sensitivity analysis.

        Parameters
        ----------
        config_path : str or Path
            Path to YAML config file.
        parameters : list of dict, optional
            Each dict: ``{"name": str, "lower": float, "upper": float}``.
            If None, uses default parameter set.
        n_samples : int
            Base sample count for Saltelli sampling.
        output_dir : str or Path
            Working directory for intermediate files.

        Returns
        -------
        SobolResult
            Object with ``S1``, ``ST`` (first/total-order indices),
            ``parameters``, ``kpi_names``. Call ``.to_csv(path)`` to export.

        Example
        -------
        >>> result = esfex.sensitivity("config.yaml", n_samples=128)
        >>> for kpi in result.kpi_names:
        ...     print(f"{kpi}: most sensitive to {result.parameters[result.ST[kpi].argmax()]}")
        """
        from esfex.sensitivity.engine import SensitivityEngine, SensitivityParameter

        config_path = str(Path(config_path))
        output_dir = str(Path(output_dir))

        if parameters is None:
            engine = SensitivityEngine(mode="config", parameters=[], n_base_samples=n_samples)
        else:
            params = [
                SensitivityParameter(
                    name=p["name"],
                    lower_bound=p.get("lower", 0.5),
                    upper_bound=p.get("upper", 1.5),
                )
                for p in parameters
            ]
            engine = SensitivityEngine(mode="config", parameters=params, n_base_samples=n_samples)

        def _progress(current, total, msg):
            print(f"  [{current}/{total}] {msg}")

        return engine.run_config_analysis(
            config_path, output_dir, progress_callback=_progress,
        )

    # ── Help ──────────────────────────────────────────────────

    @staticmethod
    def help() -> None:
        """Print a quick reference of all available scripting functions."""
        text = """
╔══════════════════════════════════════════════════════════════╗
║                  ESFEX Scripting API                        ║
╚══════════════════════════════════════════════════════════════╝

  esfex.version                  → Version string
  esfex.available_solvers        → List installed solvers

── Configuration ──────────────────────────────────────────────
  esfex.load_config(path)        → Load YAML → ESFEXConfig
  esfex.load_results(path)       → Load HDF5 → dict of arrays
  esfex.export_results(h5, dir, fmt)  → Export to csv/excel/json

── Simulation ─────────────────────────────────────────────────
  esfex.run(config, output_dir)  → Full simulation → YearResults
  esfex.solve_master(config)     → Capacity expansion only
  esfex.solve_dispatch(config)   → Operational dispatch only

── Analysis ───────────────────────────────────────────────────
  esfex.sensitivity(config_path, params, n_samples)
                                  → Sobol global sensitivity

── Workflows (standalone models) ──────────────────────────────
  esfex.workflows.help()                         → Full reference
  esfex.workflows.wind_weibull(speeds)            → Weibull (k, A)
  esfex.workflows.wind_financials(...)            → LCOE, NPV, IRR
  esfex.workflows.solar_financials(...)           → LCOE, NPV, IRR
  esfex.workflows.system_financials(h5)           → Post-opt financials
  esfex.workflows.financial_monte_carlo(h5)       → Risk distributions
  esfex.workflows.hazard_screening(coords)        → Multi-hazard screen
  esfex.workflows.risk_assessment(maps, comps)    → Composite risk
  esfex.workflows.ev_profiles(...)                → EV demand (MW/h)
  esfex.workflows.estimate_demand(...)            → ML demand estimate
  esfex.workflows.climate_demand_adjustment(...)  → HDD/CDD scaling
  esfex.workflows.otec_capacity_factors(tw, tc)   → OTEC daily CFs
  esfex.workflows.tsam_cluster(demand, n)         → Representative days

── Plotting ───────────────────────────────────────────────────
  esfex.plot.generation_mix(results, year)
  esfex.plot.prices(results, year)
  esfex.plot.investments(results)
  esfex.plot.load_duration(results, year)

── Console Objects ────────────────────────────────────────────
  model    → GUI data model (all systems, elements)
  state    → Current system state (generators, batteries...)
  config   → Loaded ESFEXConfig (None if not loaded)
  window   → MainWindow (full GUI access)
  np       → numpy

── Examples ───────────────────────────────────────────────────
  # Load and run a simulation
  cfg = esfex.load_config("my_config.yaml")
  results = esfex.run(cfg, "output/", years=5)

  # Load existing results and plot
  r = esfex.load_results("results/system_Cuba.h5")
  esfex.plot.generation_mix(r, 2025)

  # Batch parameter sweep
  cfg = esfex.load_config("config.yaml")
  for discount in [0.05, 0.08, 0.10]:
      cfg.systems["main"].discount_rate = discount
      res = esfex.run(cfg, f"output_dr{discount}/")
      print(f"DR={discount}: cost={res[-1].objective:.0f}")

  # Quick sensitivity analysis
  result = esfex.sensitivity("config.yaml", n_samples=64)
  result.to_csv("sobol_indices.csv")
"""
        print(text)

    def __repr__(self) -> str:
        return "ESFEXAPI — type esfex.help() for usage"


# Module-level singleton
esfex = ESFEXAPI()
