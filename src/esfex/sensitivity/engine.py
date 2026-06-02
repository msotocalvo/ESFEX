"""Sobol Global Sensitivity Analysis engine for ESFEX.

Supports two modes:
- LP-level: perturb objective/RHS coefficients in parsed .lp file, re-solve with scipy
- Config-level: vary YAML config parameters, re-run full optimization
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import yaml

logger = logging.getLogger(__name__)

# Default parameter ranges (multipliers)
_DEFAULT_BOUNDS = (0.5, 2.0)

# KPI names
KPI_NAMES = [
    "total_cost",
    "inv_gen_total",
    "inv_bat_total",
    "curtailment",
    "load_shedding",
]

# Config-level parameter definitions (match ScenarioMultipliers fields)
CONFIG_PARAMETERS = [
    ("invest_cost_renewables", "RE Investment Cost", (0.5, 2.0)),
    ("invest_cost_storage", "Storage Investment Cost", (0.5, 2.0)),
    ("invest_cost_conventional", "Conv. Investment Cost", (0.5, 2.0)),
    ("invest_cost_transmission", "Transmission Inv. Cost", (0.5, 2.0)),
    ("fuel_cost", "Fuel Cost", (0.5, 3.0)),
    ("maintenance_cost", "Maintenance Cost", (0.5, 2.0)),
    ("demand_growth", "Demand Growth", (0.8, 1.5)),
    ("fuel_price_growth", "Fuel Price Growth", (0.5, 2.0)),
    ("carbon_price", "Carbon Price", (0.0, 3.0)),
]


@dataclass
class SensitivityParameter:
    """A parameter to vary in sensitivity analysis."""

    name: str
    key: str
    lower_bound: float = 0.5
    upper_bound: float = 2.0
    category: str = "objective"  # "objective", "rhs", or "config"


@dataclass
class SobolResult:
    """Results from a Sobol analysis run."""

    parameters: list[str] = field(default_factory=list)
    kpi_names: list[str] = field(default_factory=list)
    S1: dict[str, np.ndarray] = field(default_factory=dict)
    ST: dict[str, np.ndarray] = field(default_factory=dict)
    S1_conf: dict[str, np.ndarray] = field(default_factory=dict)
    ST_conf: dict[str, np.ndarray] = field(default_factory=dict)
    n_samples: int = 0
    n_evaluations: int = 0

    def to_csv(self, filepath: str | Path) -> None:
        """Export Sobol indices to CSV file."""
        import csv

        filepath = Path(filepath)
        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["KPI", "Parameter", "S1", "S1_conf", "ST", "ST_conf"])
            for kpi in self.kpi_names:
                for i, param in enumerate(self.parameters):
                    writer.writerow([
                        kpi,
                        param,
                        f"{self.S1[kpi][i]:.6f}",
                        f"{self.S1_conf[kpi][i]:.6f}",
                        f"{self.ST[kpi][i]:.6f}",
                        f"{self.ST_conf[kpi][i]:.6f}",
                    ])
        logger.info(f"Sobol indices exported to {filepath}")


class SensitivityEngine:
    """Orchestrates Sobol sensitivity analysis."""

    def __init__(
        self,
        mode: str,
        parameters: list[SensitivityParameter],
        kpi_names: list[str] | None = None,
        n_base_samples: int = 128,
    ):
        self.mode = mode  # "lp" or "config"
        self.parameters = parameters
        self.kpi_names = kpi_names or list(KPI_NAMES)
        self.n_base_samples = n_base_samples

    @property
    def problem(self) -> dict:
        """SALib problem definition."""
        return {
            "num_vars": len(self.parameters),
            "names": [p.name for p in self.parameters],
            "bounds": [[p.lower_bound, p.upper_bound] for p in self.parameters],
        }

    @property
    def n_evaluations(self) -> int:
        """Total number of model evaluations required.

        generate_samples uses Saltelli with calc_second_order=False, which
        produces N*(D+2) rows (not N*(2D+2)); this must match so progress
        reporting agrees with the actual loop count.
        """
        D = len(self.parameters)
        return self.n_base_samples * (D + 2)

    def generate_samples(self) -> np.ndarray:
        """Generate Saltelli sample matrix.

        Returns array of shape (N*(2D+2), D) where each row is a parameter set.
        """
        from SALib.sample import saltelli

        return saltelli.sample(self.problem, self.n_base_samples, calc_second_order=False)

    def run_lp_analysis(
        self,
        lp_path: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> SobolResult:
        """Run LP-level Sobol analysis.

        Parses the LP file once, then perturbs and re-solves for each sample.
        """
        from esfex.sensitivity.lp_parser import (
            extract_kpis,
            parse_lp_file,
            perturb_and_solve,
            solve_lp,
        )

        if progress_callback:
            progress_callback(0, self.n_evaluations, "Parsing LP file...")

        model = parse_lp_file(lp_path)
        samples = self.generate_samples()
        n_eval = samples.shape[0]

        # Map parameters to their groups
        obj_groups = model.get_objective_groups()
        rhs_groups = model.get_rhs_groups()

        # Evaluate all samples
        evaluations: dict[str, list[float]] = {kpi: [] for kpi in self.kpi_names}

        for i in range(n_eval):
            if progress_callback:
                progress_callback(i + 1, n_eval, f"Solving perturbation {i + 1}/{n_eval}")

            # Build multiplier dicts from sample row
            obj_mults: dict[str, float] = {}
            rhs_mults: dict[str, float] = {}

            for j, param in enumerate(self.parameters):
                mult = samples[i, j]
                if param.category == "objective" and param.key in obj_groups:
                    obj_mults[param.key] = mult
                elif param.category == "rhs" and param.key in rhs_groups:
                    rhs_mults[param.key] = mult

            kpis = perturb_and_solve(model, obj_mults, rhs_mults)
            for kpi_name in self.kpi_names:
                evaluations[kpi_name].append(kpis.get(kpi_name, float("nan")))

        return self._analyze(samples, evaluations, n_eval)

    def run_config_analysis(
        self,
        base_config_path: str,
        output_dir: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> SobolResult:
        """Run config-level Sobol analysis.

        Creates modified configs and runs full simulations for each sample.
        """
        if progress_callback:
            progress_callback(0, self.n_evaluations, "Generating samples...")

        with open(base_config_path) as f:
            base_config = yaml.safe_load(f)

        samples = self.generate_samples()
        n_eval = samples.shape[0]
        output_base = Path(output_dir) / "sensitivity"
        output_base.mkdir(parents=True, exist_ok=True)

        evaluations: dict[str, list[float]] = {kpi: [] for kpi in self.kpi_names}

        for i in range(n_eval):
            if progress_callback:
                progress_callback(i + 1, n_eval, f"Running simulation {i + 1}/{n_eval}")

            # Create modified config
            config = _apply_config_multipliers(base_config, self.parameters, samples[i])

            # Write temp config
            tmp = tempfile.NamedTemporaryFile(
                suffix=".yaml", prefix=f"sa_run_{i}_", delete=False, dir=str(output_base),
            )
            tmp_path = tmp.name
            tmp.close()
            with open(tmp_path, "w") as f:
                yaml.safe_dump(config, f, default_flow_style=False)

            # Run simulation
            run_output = str(output_base / f"run_{i:04d}")
            kpis = _run_simulation_and_extract(tmp_path, run_output)

            for kpi_name in self.kpi_names:
                evaluations[kpi_name].append(kpis.get(kpi_name, float("nan")))

            # Clean up temp config
            Path(tmp_path).unlink(missing_ok=True)

        return self._analyze(samples, evaluations, n_eval)

    def _analyze(
        self,
        samples: np.ndarray,
        evaluations: dict[str, list[float]],
        n_eval: int,
    ) -> SobolResult:
        """Compute Sobol indices from evaluations."""
        from SALib.analyze import sobol

        result = SobolResult(
            parameters=[p.name for p in self.parameters],
            kpi_names=list(self.kpi_names),
            n_samples=self.n_base_samples,
            n_evaluations=n_eval,
        )

        for kpi_name in self.kpi_names:
            Y = np.array(evaluations[kpi_name])

            # Replace inf/nan with large value to avoid SALib errors
            finite_mask = np.isfinite(Y)
            if not finite_mask.all():
                max_finite = np.nanmax(Y[finite_mask]) if finite_mask.any() else 1e12
                Y = np.where(finite_mask, Y, max_finite * 10)

            # Check for zero variance
            if np.std(Y) < 1e-15:
                n_params = len(self.parameters)
                result.S1[kpi_name] = np.zeros(n_params)
                result.ST[kpi_name] = np.zeros(n_params)
                result.S1_conf[kpi_name] = np.zeros(n_params)
                result.ST_conf[kpi_name] = np.zeros(n_params)
                continue

            Si = sobol.analyze(
                self.problem, Y, calc_second_order=False, print_to_console=False,
            )
            result.S1[kpi_name] = Si["S1"]
            result.ST[kpi_name] = Si["ST"]
            result.S1_conf[kpi_name] = Si["S1_conf"]
            result.ST_conf[kpi_name] = Si["ST_conf"]

        return result


def get_lp_parameters(lp_path: str) -> list[SensitivityParameter]:
    """Auto-detect available parameters from an LP file.

    Returns a list of SensitivityParameter with default bounds.
    """
    from esfex.sensitivity.lp_parser import parse_lp_file

    model = parse_lp_file(lp_path)
    params: list[SensitivityParameter] = []

    # Objective coefficient groups
    for group_name in sorted(model.get_objective_groups()):
        display = group_name.replace("_", " ").title()
        params.append(SensitivityParameter(
            name=display, key=group_name,
            lower_bound=0.5, upper_bound=2.0, category="objective",
        ))

    # RHS groups
    for group_name in sorted(model.get_rhs_groups()):
        display = group_name.replace("_", " ").title()
        params.append(SensitivityParameter(
            name=display, key=group_name,
            lower_bound=0.8, upper_bound=1.5, category="rhs",
        ))

    return params


def get_config_parameters() -> list[SensitivityParameter]:
    """Return predefined config-level parameters."""
    return [
        SensitivityParameter(
            name=display, key=key,
            lower_bound=bounds[0], upper_bound=bounds[1],
            category="config",
        )
        for key, display, bounds in CONFIG_PARAMETERS
    ]


def _iter_components(container) -> list[dict]:
    """Yield component dicts whether the container is a list or a name->dict map.

    ESFEX configs (e.g. cuba.yaml) organize generators/technologies/batteries
    as name-keyed dicts; older/simpler configs may use lists.
    """
    if isinstance(container, dict):
        return [v for v in container.values() if isinstance(v, dict)]
    if isinstance(container, list):
        return [v for v in container if isinstance(v, dict)]
    return []


def _scale_field(obj: dict, key: str, mult: float) -> None:
    """Multiply a scalar or per-element-list numeric field in place."""
    if key not in obj or obj[key] is None:
        return
    v = obj[key]
    if isinstance(v, list):
        obj[key] = [x * mult if isinstance(x, (int, float)) else x for x in v]
    elif isinstance(v, (int, float)) and not isinstance(v, bool):
        obj[key] = v * mult


def _is_renewable(component: dict) -> bool:
    """Classify a generator/technology as variable renewable (solar/wind/PV)."""
    hints = " ".join(
        str(component.get(k, "")) for k in ("type", "technology", "fuel", "name")
    ).lower()
    return any(t in hints for t in ("solar", "wind", "pv", "photovolt"))


def _apply_config_multipliers(
    base_config: dict,
    parameters: list[SensitivityParameter],
    sample_row: np.ndarray,
) -> dict:
    """Apply parameter multipliers to a config dict.

    Scales the cost fields where investment actually lives in the current
    schema: new capacity is invested through ``technologies`` (and
    ``battery_technologies``); ``generators``/``batteries`` describe existing
    units. Components may be name-keyed dicts or lists; both are handled.

    Unmapped multipliers (invest_cost_transmission, maintenance_cost,
    demand_growth, fuel_price_growth, carbon_price) are intentionally left as
    no-ops here because cuba.yaml has no single field they map to cleanly;
    they should be wired explicitly if/when those sweeps are needed.
    """
    import copy
    config = copy.deepcopy(base_config)

    multipliers = {p.key: float(sample_row[i]) for i, p in enumerate(parameters)}
    re_mult = multipliers.get("invest_cost_renewables")
    conv_mult = multipliers.get("invest_cost_conventional")
    fuel_mult = multipliers.get("fuel_cost")
    storage_mult = multipliers.get("invest_cost_storage")
    demand_mult = multipliers.get("demand_growth")

    for sys_config in config.get("systems", {}).values():
        if not isinstance(sys_config, dict):
            continue

        # Investment cost of generation: split renewable vs conventional.
        # New capacity lives in `technologies`; existing units in `generators`.
        for comp_key in ("technologies", "generators"):
            for comp in _iter_components(sys_config.get(comp_key)):
                mult = re_mult if _is_renewable(comp) else conv_mult
                if mult is not None:
                    _scale_field(comp, "invest_cost", mult)
                if fuel_mult is not None:
                    _scale_field(comp, "fuel_cost", fuel_mult)

        # Storage investment cost: battery_technologies (investable) + batteries.
        if storage_mult is not None:
            for comp_key in ("battery_technologies", "batteries"):
                for bat in _iter_components(sys_config.get(comp_key)):
                    for field_key in ("invest_cost", "invest_cost_power",
                                      "invest_cost_energy", "invest_cost_capacity"):
                        _scale_field(bat, field_key, storage_mult)

        # Demand growth (applied when sys_config carries a top-level demand.growth_rate).
        if demand_mult is not None:
            demand_cfg = sys_config.get("demand")
            if isinstance(demand_cfg, dict):
                _scale_field(demand_cfg, "growth_rate", demand_mult)

    return config


def _run_simulation_and_extract(config_path: str, output_dir: str) -> dict[str, float]:
    """Run a single ESFEX simulation and extract KPIs from results."""
    kpis = {kpi: float("nan") for kpi in KPI_NAMES}

    try:
        result = subprocess.run(
            [sys.executable, "-m", "esfex.cli", "run",
             "--config", config_path, "--output", output_dir],
            capture_output=True, text=True, timeout=3600,
        )

        if result.returncode != 0:
            logger.warning(f"Simulation failed: {result.stderr[:200]}")
            return kpis

        # Try to extract KPIs from HDF5 results
        kpis = _extract_kpis_from_results(output_dir)

    except subprocess.TimeoutExpired:
        logger.warning(f"Simulation timed out for {config_path}")
    except Exception as e:
        logger.warning(f"Simulation error: {e}")

    return kpis


def _extract_kpis_from_results(output_dir: str) -> dict[str, float]:
    """Extract KPI values from the HDF5 results written by an ESFEX run.

    The runner names outputs ``esfex_run_*.h5`` and stores year-indexed
    aggregates under the top-level ``summary_results`` group (total_cost,
    loss_of_load) with curtailment living per-year under ``detailed_results``.
    """
    import h5py

    kpis = {kpi: float("nan") for kpi in KPI_NAMES}
    output_path = Path(output_dir)

    h5_files: list[Path] = []
    for pattern in ("esfex_run_*.h5", "results_*.h5", "*.h5",
                    "**/esfex_run_*.h5", "**/*.h5"):
        h5_files = sorted(output_path.glob(pattern))
        if h5_files:
            break
    if not h5_files:
        logger.warning(f"No HDF5 results found under {output_dir}")
        return kpis

    total_cost = 0.0
    load_shedding = 0.0
    curtailment = 0.0
    found_summary = False
    found_curt = False

    for h5_path in h5_files:
        try:
            with h5py.File(h5_path, "r") as f:
                summary = f.get("summary_results")
                if summary is not None:
                    if "total_cost" in summary:
                        total_cost += float(np.sum(summary["total_cost"][...]))
                        found_summary = True
                    if "loss_of_load" in summary:
                        load_shedding += float(np.sum(summary["loss_of_load"][...]))

                detailed = f.get("detailed_results")
                if detailed is not None:
                    for yname in detailed:
                        yg = detailed[yname]
                        if isinstance(yg, h5py.Group) and "curtailment" in yg:
                            curtailment += float(np.sum(yg["curtailment"][...]))
                            found_curt = True
        except Exception as e:
            logger.warning(f"Error reading {h5_path}: {e}")

    if found_summary:
        kpis["total_cost"] = total_cost
        kpis["load_shedding"] = load_shedding
    if found_curt:
        kpis["curtailment"] = curtailment
    # inv_gen_total / inv_bat_total are not exported as scalar summaries by the
    # runner, so they remain NaN here; the analyzer flattens zero-variance KPIs.
    # Wire them only if the runner gains an investment-MW summary dataset.

    return kpis
