"""
Solver configuration for ESFEX optimization models.

Provides factory functions to create configured solver instances
for JuMP (Julia) backend.

Supports HiGHS (recommended), Gurobi, and CPLEX solvers
with optimized settings for numerical stability.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Optional

import psutil

from esfex.config.schema import SolverConfig

logger = logging.getLogger(__name__)

# ── Solver-specific option definitions ────────────────────────────
# Each entry: {key, label, type, attr (JuMP attribute name), ...}

SOLVER_OPTIONS: dict[str, list[dict[str, Any]]] = {
    "highs": [
        {
            "key": "presolve", "label": "Presolve", "type": "combo",
            "choices": ["off", "on", "choose"], "default": "choose",
            "attr": "presolve",
        },
        {
            "key": "solver_method", "label": "LP/QP Solver", "type": "combo",
            "choices": ["choose", "simplex", "ipm", "ipx", "hipo", "pdlp"],
            "default": "choose",
            "attr": "solver",
        },
        {
            "key": "simplex_strategy", "label": "Simplex Strategy", "type": "combo",
            "choices": ["choose", "dual_serial", "dual_pami", "dual_sip", "primal"],
            "values": [0, 1, 2, 3, 4], "default": "choose",
            "attr": "simplex_strategy",
            "enabled_when": {"solver_method": ["choose", "simplex"]},
        },
        {
            "key": "parallel", "label": "Parallel", "type": "combo",
            "choices": ["choose", "off", "on"], "default": "choose",
            "attr": "parallel",
        },
        {
            "key": "run_crossover", "label": "Run Crossover", "type": "combo",
            "choices": ["off", "choose", "on"], "default": "on",
            "attr": "run_crossover",
            "enabled_when": {"solver_method": ["choose", "ipm", "ipx", "hipo"]},
        },
        {
            "key": "primal_feasibility_tolerance", "label": "Primal Tol.",
            "type": "float", "min": 1e-10, "max": 1.0, "decimals": 8,
            "default": 1e-7, "attr": "primal_feasibility_tolerance",
            "enabled_when": {"solver_method": ["choose", "simplex"]},
        },
        {
            "key": "dual_feasibility_tolerance", "label": "Dual Tol.",
            "type": "float", "min": 1e-10, "max": 1.0, "decimals": 8,
            "default": 1e-7, "attr": "dual_feasibility_tolerance",
            "enabled_when": {"solver_method": ["choose", "simplex"]},
        },
        {
            "key": "ipm_optimality_tolerance", "label": "IPM Tol.",
            "type": "float", "min": 1e-12, "max": 1.0, "decimals": 10,
            "default": 1e-8, "attr": "ipm_optimality_tolerance",
            "enabled_when": {"solver_method": ["choose", "ipm", "ipx", "hipo"]},
        },
        {
            "key": "pdlp_scaling", "label": "PDLP Scaling", "type": "combo",
            "choices": ["off", "on"],
            "values": [False, True], "default": "on",
            "attr": "pdlp_scaling",
            "enabled_when": {"solver_method": ["choose", "pdlp"]},
        },
        {
            "key": "pdlp_iteration_limit", "label": "PDLP Iter. Limit",
            "type": "int", "min": 0, "max": 2_147_483_647,
            "default": 2_147_483_647, "attr": "pdlp_iteration_limit",
            "enabled_when": {"solver_method": ["choose", "pdlp"]},
        },
        {
            "key": "simplex_iteration_limit", "label": "Simplex Iter. Limit",
            "type": "int", "min": 0, "max": 2_147_483_647,
            "default": 2_147_483_647, "attr": "simplex_iteration_limit",
            "enabled_when": {"solver_method": ["choose", "simplex"]},
        },
        {
            "key": "simplex_scale_strategy", "label": "Scaling", "type": "combo",
            "choices": ["off", "choose", "forced_equilibration",
                        "mean_equilibration", "max_equilibration",
                        "max_value_0", "max_value_1"],
            "values": [0, 1, 2, 3, 4, 5, 6], "default": "choose",
            "attr": "simplex_scale_strategy",
            "enabled_when": {"solver_method": ["choose", "simplex"]},
        },
    ],
    "gurobi": [
        {
            "key": "method", "label": "Method", "type": "combo",
            "choices": ["auto", "primal_simplex", "dual_simplex",
                        "barrier", "concurrent"],
            "values": [-1, 0, 1, 2, 3], "default": "auto",
            "attr": "Method",
        },
        {
            "key": "presolve", "label": "Presolve", "type": "combo",
            "choices": ["auto", "off", "conservative", "aggressive"],
            "values": [-1, 0, 1, 2], "default": "auto",
            "attr": "Presolve",
        },
        {
            "key": "crossover", "label": "Crossover", "type": "combo",
            "choices": ["auto", "off"],
            "values": [-1, 0], "default": "auto",
            "attr": "Crossover",
            "enabled_when": {"method": ["auto", "barrier", "concurrent"]},
        },
        {
            "key": "numeric_focus", "label": "Numeric Focus", "type": "combo",
            "choices": ["auto", "moderate", "aggressive"],
            "values": [0, 1, 2], "default": "auto",
            "attr": "NumericFocus",
        },
        {
            "key": "scale_flag", "label": "Scaling", "type": "combo",
            "choices": ["auto", "off", "moderate", "aggressive"],
            "values": [-1, 0, 1, 2], "default": "auto",
            "attr": "ScaleFlag",
        },
        {
            "key": "bar_conv_tol", "label": "Barrier Conv. Tol.",
            "type": "float", "min": 1e-12, "max": 1.0, "decimals": 10,
            "default": 1e-8, "attr": "BarConvTol",
            "enabled_when": {"method": ["auto", "barrier", "concurrent"]},
        },
        {
            "key": "feasibility_tol", "label": "Feasibility Tol.",
            "type": "float", "min": 1e-9, "max": 1e-2, "decimals": 10,
            "default": 1e-6, "attr": "FeasibilityTol",
        },
        {
            "key": "optimality_tol", "label": "Optimality Tol.",
            "type": "float", "min": 1e-9, "max": 1e-2, "decimals": 10,
            "default": 1e-6, "attr": "OptimalityTol",
        },
        {
            "key": "heuristics", "label": "Heuristics", "type": "float",
            "min": 0.0, "max": 1.0, "decimals": 2, "default": 0.05,
            "attr": "Heuristics",
        },
        {
            "key": "iteration_limit", "label": "Iteration Limit",
            "type": "int", "min": 0, "max": 2_147_483_647,
            "default": 2_147_483_647, "attr": "IterationLimit",
            # Simplex iteration cap — barrier uses its own (BarIterLimit).
            "enabled_when": {"method": ["auto", "primal_simplex",
                                        "dual_simplex", "concurrent"]},
        },
    ],
    "cplex": [
        {
            "key": "lp_method", "label": "LP Method", "type": "combo",
            "choices": ["auto", "primal_simplex", "dual_simplex",
                        "barrier", "network"],
            "values": [0, 1, 2, 4, 5], "default": "auto",
            "attr": "CPXPARAM_LPMethod",
        },
        {
            "key": "presolve", "label": "Presolve", "type": "combo",
            "choices": ["on", "off"],
            "values": [1, 0], "default": "on",
            "attr": "CPXPARAM_Preprocessing_Presolve",
        },
        {
            "key": "numerical_emphasis", "label": "Numerical Emphasis",
            "type": "combo",
            "choices": ["off", "on"],
            "values": [0, 1], "default": "off",
            "attr": "CPXPARAM_Emphasis_Numerical",
        },
        {
            "key": "scale", "label": "Scaling", "type": "combo",
            "choices": ["equilibration", "off", "aggressive"],
            "values": [0, -1, 1], "default": "equilibration",
            "attr": "CPXPARAM_Read_Scale",
        },
        {
            "key": "feasibility_tol", "label": "Feasibility Tol.",
            "type": "float", "min": 1e-9, "max": 1e-1, "decimals": 10,
            "default": 1e-6, "attr": "CPXPARAM_Simplex_Tolerances_Feasibility",
            # Simplex-specific tolerance (CPXPARAM_Simplex_Tolerances_*).
            "enabled_when": {"lp_method": ["auto", "primal_simplex",
                                           "dual_simplex", "network"]},
        },
        {
            "key": "optimality_tol", "label": "Optimality Tol.",
            "type": "float", "min": 1e-9, "max": 1e-1, "decimals": 10,
            "default": 1e-6, "attr": "CPXPARAM_Simplex_Tolerances_Optimality",
            "enabled_when": {"lp_method": ["auto", "primal_simplex",
                                           "dual_simplex", "network"]},
        },
        {
            "key": "barrier_conv_tol", "label": "Barrier Conv. Tol.",
            "type": "float", "min": 1e-12, "max": 1e-1, "decimals": 12,
            "default": 1e-8, "attr": "CPXPARAM_Barrier_ConvergeTol",
            "enabled_when": {"lp_method": ["auto", "barrier"]},
        },
        {
            "key": "mip_emphasis", "label": "MIP Emphasis", "type": "combo",
            "choices": ["balanced", "feasibility", "optimality",
                        "best_bound", "hidden_feasibility"],
            "values": [0, 1, 2, 3, 4], "default": "balanced",
            "attr": "CPXPARAM_Emphasis_MIP",
        },
    ],
    "glpk": [
        {
            "key": "msg_lev", "label": "Message Level", "type": "combo",
            "choices": ["off", "errors", "normal", "verbose"],
            "values": [0, 1, 2, 3], "default": "normal",
            "attr": "msg_lev",
        },
        {
            "key": "meth", "label": "LP Method", "type": "combo",
            "choices": ["primal_simplex", "dual_simplex", "dual_primal"],
            "values": [1, 2, 3], "default": "primal_simplex",
            "attr": "meth",
        },
        {
            "key": "presolve", "label": "Presolve", "type": "combo",
            "choices": ["off", "on"],
            "values": [0, 1], "default": "on",
            "attr": "presolve",
        },
        {
            "key": "tol_bnd", "label": "Primal Tol.",
            "type": "float", "min": 1e-12, "max": 1e-1, "decimals": 10,
            "default": 1e-7, "attr": "tol_bnd",
        },
        {
            "key": "tol_dj", "label": "Dual Tol.",
            "type": "float", "min": 1e-12, "max": 1e-1, "decimals": 10,
            "default": 1e-7, "attr": "tol_dj",
        },
        {
            "key": "tol_piv", "label": "Pivot Tol.",
            "type": "float", "min": 1e-12, "max": 1.0, "decimals": 10,
            "default": 1e-10, "attr": "tol_piv",
        },
        {
            "key": "it_lim", "label": "Iteration Limit",
            "type": "int", "min": 0, "max": 2_147_483_647,
            "default": 2_147_483_647, "attr": "it_lim",
        },
        {
            "key": "mip_gap", "label": "MIP Gap",
            "type": "float", "min": 0.0, "max": 1.0, "decimals": 6,
            "default": 0.0, "attr": "mip_gap",
        },
    ],
    "cbc": [
        {
            "key": "logLevel", "label": "Log Level", "type": "combo",
            "choices": ["off", "minimal", "normal", "verbose"],
            "values": [0, 1, 2, 3], "default": "minimal",
            "attr": "logLevel",
        },
        {
            "key": "primalTolerance", "label": "Primal Tol.",
            "type": "float", "min": 1e-10, "max": 1.0, "decimals": 8,
            "default": 1e-7, "attr": "primalTolerance",
        },
        {
            "key": "dualTolerance", "label": "Dual Tol.",
            "type": "float", "min": 1e-10, "max": 1.0, "decimals": 8,
            "default": 1e-7, "attr": "dualTolerance",
        },
        {
            "key": "ratioGap", "label": "MIP Gap",
            "type": "float", "min": 0.0, "max": 1.0, "decimals": 6,
            "default": 0.0, "attr": "ratioGap",
        },
    ],
    "scip": [
        {
            "key": "display/verblevel", "label": "Verbosity", "type": "combo",
            "choices": ["off", "errors", "warnings", "normal", "full"],
            "values": [0, 1, 2, 3, 4], "default": "off",
            "attr": "display/verblevel",
        },
        {
            "key": "presolving/maxrounds", "label": "Presolve Rounds", "type": "combo",
            "choices": ["off", "default", "aggressive"],
            "values": [0, -1, 100], "default": "default",
            "attr": "presolving/maxrounds",
        },
        {
            "key": "separating/maxrounds", "label": "Separating Rounds", "type": "combo",
            "choices": ["off", "default", "aggressive"],
            "values": [0, -1, 20], "default": "default",
            "attr": "separating/maxrounds",
        },
        {
            "key": "numerics/feastol", "label": "Feasibility Tol.",
            "type": "float", "min": 1e-12, "max": 1e-1, "decimals": 10,
            "default": 1e-6, "attr": "numerics/feastol",
        },
        {
            "key": "numerics/dualfeastol", "label": "Dual Feas. Tol.",
            "type": "float", "min": 1e-12, "max": 1e-1, "decimals": 10,
            "default": 1e-7, "attr": "numerics/dualfeastol",
        },
        {
            "key": "lp/scaling", "label": "LP Scaling", "type": "combo",
            "choices": ["off", "on"],
            "values": [0, 1], "default": "on",
            "attr": "lp/scaling",
        },
    ],
    "xpress": [
        {
            "key": "OUTPUTLOG", "label": "Output Log", "type": "combo",
            "choices": ["off", "on"],
            "values": [0, 1], "default": "off",
            "attr": "OUTPUTLOG",
        },
        {
            "key": "PRESOLVE", "label": "Presolve", "type": "combo",
            "choices": ["off", "on"],
            "values": [0, 1], "default": "on",
            "attr": "PRESOLVE",
        },
        {
            "key": "DEFAULTALG", "label": "LP Algorithm", "type": "combo",
            "choices": ["auto", "dual_simplex", "primal_simplex", "barrier"],
            "values": [1, 2, 3, 4], "default": "auto",
            "attr": "DEFAULTALG",
        },
        {
            "key": "SCALING", "label": "Scaling", "type": "combo",
            "choices": ["off", "row_col", "aggressive"],
            "values": [0, 3, 35], "default": "row_col",
            "attr": "SCALING",
        },
        {
            "key": "FEASTOL", "label": "Feasibility Tol.",
            "type": "float", "min": 1e-12, "max": 1e-1, "decimals": 10,
            "default": 1e-6, "attr": "FEASTOL",
        },
        {
            "key": "OPTIMALITYTOL", "label": "Optimality Tol.",
            "type": "float", "min": 1e-12, "max": 1e-1, "decimals": 10,
            "default": 1e-6, "attr": "OPTIMALITYTOL",
        },
        {
            "key": "BARGAPSTOP", "label": "Barrier Gap Stop",
            "type": "float", "min": 1e-12, "max": 1.0, "decimals": 10,
            "default": 1e-8, "attr": "BARGAPSTOP",
            "enabled_when": {"DEFAULTALG": ["auto", "barrier"]},
        },
    ],
}

# Solver-method selections under which the global "Threads" setting has no
# effect (serial LP algorithms — primal/dual simplex). The GUI greys out the
# Threads field accordingly. Keyed by solver; maps the controlling option key
# → the list of choice labels for which threads are inert. A solver mapped to
# an empty dict is single-threaded regardless of method (threads always inert).
# A solver absent from this map keeps Threads always enabled.
THREADS_INERT_WHEN: dict[str, dict[str, list[str]]] = {
    "highs":  {"solver_method": ["simplex"]},
    "gurobi": {"method": ["primal_simplex", "dual_simplex"]},
    "cplex":  {"lp_method": ["primal_simplex", "dual_simplex", "network"]},
    "xpress": {"DEFAULTALG": ["dual_simplex", "primal_simplex"]},
    "glpk":   {},  # GLPK has no parallel mode — threads always inert
}

# Solvers compatible with each OPF formulation (``power_flow_mode``). The
# runner uses the configured solver verbatim — there is NO internal override —
# so picking an incompatible solver makes the operational solve fail. The GUI
# greys out incompatible solvers once a formulation is selected.
#   dcopf / dcopf_ac_verify : linear program → any LP/MIP solver
#   acopf_soc / acopf_qc    : convex SOCP/QC  → conic-capable solvers
#   acopf_sdp               : semidefinite    → SDP-capable conic solvers
#   acopf_polar / acopf_rect: exact nonlinear → a general NLP solver (Ipopt)
FORMULATION_SOLVERS: dict[str, set[str]] = {
    "dcopf":           {"highs", "gurobi", "glpk", "cbc", "scip", "cplex", "xpress"},
    "dcopf_ac_verify": {"highs", "gurobi", "glpk", "cbc", "scip", "cplex", "xpress"},
    "acopf_soc":       {"highs", "gurobi", "cplex", "xpress", "clarabel", "scs"},
    "acopf_qc":        {"highs", "gurobi", "cplex", "xpress", "clarabel", "scs"},
    "acopf_sdp":       {"scs", "clarabel"},
    "acopf_polar":     {"ipopt"},
    "acopf_rect":      {"ipopt"},
}

# ── Lightweight solver availability detection ─────────────────────

_solver_cache: dict[str, bool] | None = None


def detect_available_solvers() -> dict[str, bool]:
    """Return ``{solver_name: is_available}`` for all known solvers.

    Uses lightweight Python-side checks (importability of companion
    packages) so the GUI can populate the combo without starting Julia.
    HiGHS and GLPK are always available (bundled with Julia).
    Results are cached for the session.
    """
    global _solver_cache
    if _solver_cache is not None:
        return dict(_solver_cache)

    result: dict[str, bool] = {
        "highs": True,   # HiGHS.jl always present in Project.toml
        "glpk": True,    # GLPK.jl always present
    }

    # Gurobi – check for Python gurobipy (almost always installed alongside)
    result["gurobi"] = _can_import("gurobipy")

    # CPLEX – check for Python cplex
    result["cplex"] = _can_import("cplex")

    # CBC – check for Python cylp or coinor
    result["cbc"] = _can_import("cylp") or _can_import("coinor")

    # SCIP – check for Python pyscipopt
    result["scip"] = _can_import("pyscipopt")

    # Xpress – check for Python xpress
    result["xpress"] = _can_import("xpress")

    # Ipopt / SCS / Clarabel — bundled Julia packages (declared in the Julia
    # Project.toml), so always available like HiGHS/GLPK. Required for the
    # AC-OPF formulations: Ipopt for the exact NLP modes (acopf_polar/rect),
    # SCS & Clarabel for the SDP relaxation (acopf_sdp) and conic SOCP/QC.
    result["ipopt"] = True
    result["scs"] = True
    result["clarabel"] = True

    _solver_cache = result
    return dict(result)


def _can_import(module_name: str) -> bool:
    """Return True if *module_name* can be imported."""
    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        return False


def solver_option_to_julia_value(opt: dict[str, Any], value: Any) -> str:
    """Convert a GUI option value to a Julia literal string."""
    if opt["type"] == "combo" and "values" in opt:
        idx = opt["choices"].index(value) if value in opt["choices"] else 0
        return str(opt["values"][idx])
    if opt["type"] == "float":
        return str(float(value))
    if opt["type"] == "int":
        return str(int(value))
    if opt["type"] == "bool":
        return "true" if value else "false"
    # String value (HiGHS uses plain strings like "on", "simplex")
    return f'"{value}"'


def get_julia_optimizer_string(config: Optional[SolverConfig] = None) -> str:
    """Generate Julia code string to create a configured optimizer.

    Includes generic options (threads, time_limit, gap, verbose) and
    any solver-specific options stored in ``config.options``.
    """
    if config is None:
        config = SolverConfig()

    solver_name = config.name.lower()

    # Base attributes per solver
    if solver_name == "highs":
        pkg, attrs = "HiGHS", [
            f'"threads" => {config.threads}',
            f'"time_limit" => {float(config.time_limit)}',
            f'"mip_rel_gap" => {config.gap}',
            f'"output_flag" => {str(config.verbose).lower()}',
        ]
    elif solver_name == "gurobi":
        pkg, attrs = "Gurobi", [
            f'"Threads" => {config.threads}',
            f'"TimeLimit" => {float(config.time_limit)}',
            f'"MIPGap" => {config.gap}',
            f'"OutputFlag" => {1 if config.verbose else 0}',
        ]
    elif solver_name == "cplex":
        pkg, attrs = "CPLEX", [
            f'"CPXPARAM_Threads" => {config.threads}',
            f'"CPXPARAM_TimeLimit" => {float(config.time_limit)}',
            f'"CPXPARAM_MIP_Tolerances_MIPGap" => {config.gap}',
        ]
    elif solver_name == "glpk":
        pkg, attrs = "GLPK", [
            f'"tm_lim" => {int(config.time_limit * 1000)}',
            f'"mip_gap" => {config.gap}',
            f'"msg_lev" => {3 if config.verbose else 0}',
        ]
    elif solver_name == "cbc":
        pkg, attrs = "Cbc", [
            f'"seconds" => {float(config.time_limit)}',
            f'"ratioGap" => {config.gap}',
            f'"logLevel" => {1 if config.verbose else 0}',
            f'"threads" => {config.threads}',
        ]
    elif solver_name == "scip":
        pkg, attrs = "SCIP", [
            f'"limits/time" => {float(config.time_limit)}',
            f'"limits/gap" => {config.gap}',
            f'"display/verblevel" => {4 if config.verbose else 0}',
        ]
    elif solver_name == "xpress":
        pkg, attrs = "Xpress", [
            f'"THREADS" => {config.threads}',
            f'"MAXTIME" => {int(config.time_limit)}',
            f'"MIPRELSTOP" => {config.gap}',
            f'"OUTPUTLOG" => {1 if config.verbose else 0}',
        ]
    else:
        # Fallback to HiGHS
        pkg, attrs = "HiGHS", [
            f'"threads" => {config.threads}',
            f'"time_limit" => {float(config.time_limit)}',
            f'"mip_rel_gap" => {config.gap}',
            f'"output_flag" => {str(config.verbose).lower()}',
        ]

    # Append solver-specific options from config.options
    solver_opts = SOLVER_OPTIONS.get(solver_name, [])
    for opt in solver_opts:
        key = opt["key"]
        if key in config.options:
            val_str = solver_option_to_julia_value(opt, config.options[key])
            attrs.append(f'"{opt["attr"]}" => {val_str}')

    attrs_str = ",\n            ".join(attrs)
    return f"""
        optimizer_with_attributes(
            {pkg}.Optimizer,
            {attrs_str}
        )
        """


def get_solver_info(solver_name: str) -> dict:
    """
    Get information about a solver and its availability via Julia/JuMP.

    Args:
        solver_name: Name of solver to check

    Returns:
        Dictionary with solver info
    """
    info = {
        "name": solver_name,
        "available": False,
        "version": None,
        "supports_mip": True,
        "supports_lp": True,
        "backend": "Julia/JuMP",
    }

    solver_name = solver_name.lower()

    try:
        from esfex.bridge.julia_setup import get_julia

        jl = get_julia()

        if solver_name == "highs":
            jl.seval("using HiGHS")
            info["available"] = True
            info["interface"] = "HiGHS.jl"
            try:
                version = jl.seval("string(pkgversion(HiGHS))")
                info["version"] = str(version)
            except Exception:
                pass

        elif solver_name == "gurobi":
            try:
                jl.seval("using Gurobi")
                info["available"] = True
                info["interface"] = "Gurobi.jl"
            except Exception:
                info["available"] = False

        elif solver_name == "cplex":
            try:
                jl.seval("using CPLEX")
                info["available"] = True
                info["interface"] = "CPLEX.jl"
            except Exception:
                info["available"] = False

        elif solver_name == "scip":
            try:
                jl.seval("import SCIP")
                info["available"] = True
                info["interface"] = "SCIP.jl"
            except Exception:
                info["available"] = False

        elif solver_name == "xpress":
            try:
                jl.seval("import Xpress")
                info["available"] = True
                info["interface"] = "Xpress.jl"
            except Exception:
                info["available"] = False

    except Exception as e:
        logger.warning(f"Error checking solver {solver_name}: {e}")

    return info


def get_available_threads() -> int:
    """
    Get the number of available CPU threads, reserving 2 for the system.

    Returns:
        Number of threads to use for optimization
    """
    return max(1, psutil.cpu_count(logical=True) - 2)


def check_julia_solver_available(solver_name: str = "highs") -> bool:
    """
    Check if a solver is available in Julia.

    Args:
        solver_name: Solver name to check

    Returns:
        True if solver is available
    """
    try:
        info = get_solver_info(solver_name)
        return info["available"]
    except Exception:
        return False


# Numerical stability recommendations
NUMERICAL_STABILITY_GUIDE = """
Numerical Stability Guide for Optimization Models
==================================================

1. COEFFICIENT RANGE
   - Keep all coefficients in range [1e-4, 1e6]
   - Ratio of max/min should be < 1e6 (ideally < 1e4)
   - Avoid mixing very large (>1e6) and very small (<1e-6) values

2. SCALING RECOMMENDATIONS
   - Power variables: Scale by 100 MW (1000 MW → 10 units)
   - Cost variables: Scale by $1000 ($1M → 1000 units)
   - Energy variables: Scale by 100 MWh
   - Penalties: Keep in range [1, 1000]

3. BIG-M CONSTRAINTS
   - Use tight Big-M values specific to each constraint
   - Avoid single global Big-M (sum of all demand)
   - Example: M_gen = 1.1 * max_generator_capacity

4. CONSTRAINT FORMULATION
   - Normalize constraints by dividing by RHS when possible
   - Avoid constraints where LHS >> RHS or LHS << RHS
   - Use indicator constraints instead of Big-M when available

5. SOLVER SETTINGS
   - Enable presolve (reduces problem size)
   - Enable scaling (automatic coefficient normalization)
   - Use interior point method (IPM) for large LP problems
   - Set appropriate tolerances (1e-7 is usually sufficient)
"""
