"""Generate paper-ready validation plots for IEEE bus system DCOPF benchmarks.

Solves all IEEE standard systems (9, 14, 30, 57, 118, 300-bus) using ESFEX's
Julia-backed DCOPF solver and compares against multiple reference solvers:
scipy LP, PyPSA, pandapower, PYPOWER, and optionally PowerModels.jl.

Produces 4 figures (2 subplots each) as PDF.

Each (system, solver) combination runs as an independent task in its own
process. With 6 systems x 5 solvers = 30 tasks, all run truly in parallel
on machines with sufficient cores.

Usage:
    python tests/ieee_validation_plots.py [--output-dir results/ieee_validation]
    python tests/ieee_validation_plots.py --sequential   # disable parallelism

Requires Julia, esfex, and scipy to be installed.
Optional: pypsa, pandapower, pypower (pip install esfex[benchmark]).
"""

from __future__ import annotations

import argparse
import gc
import multiprocessing as mp
import sys
import threading
import time
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import matplotlib.colors as mcolors
import numpy as np
from scipy.cluster.hierarchy import dendrogram, linkage
from scipy.spatial.distance import pdist, squareform

# Add project root to path so we can import from tests/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))
sys.path.insert(0, str(_PROJECT_ROOT))

from tests.fixtures.ieee_bus_data import (
    compute_dc_opf_reference,
    ieee_9bus,
    ieee_14bus,
    ieee_30bus,
    ieee_57bus,
)
# ── Style constants ──────────────────────────────────────────────────────

SYSTEM_COLORS = {
    "IEEE 9-bus": "#2c3e50",       # dark slate
    "IEEE 14-bus": "#c0392b",      # muted red
    "IEEE 30-bus": "#2980b9",      # steel blue
    "IEEE 57-bus": "#27ae60",      # forest green
    "IEEE 118-bus": "#8e44ad",     # plum
    "IEEE 300-bus": "#d35400",     # burnt orange
    "PEGASE 1354-bus": "#16a085",  # teal
    "PEGASE 2869-bus": "#7f8c8d",  # grey
    "PEGASE 9241-bus": "#f39c12",  # amber
    "PEGASE 13659-bus": "#e74c3c", # crimson
}

SYSTEM_MARKERS = {
    "IEEE 9-bus": "o",
    "IEEE 14-bus": "s",
    "IEEE 30-bus": "^",
    "IEEE 57-bus": "D",
    "IEEE 118-bus": "P",
    "IEEE 300-bus": "X",
    "PEGASE 1354-bus": "v",
    "PEGASE 2869-bus": "h",
    "PEGASE 9241-bus": "*",
    "PEGASE 13659-bus": "d",
}

SOLVER_COLORS = {
    "scipy": "#5b9bd5",
    "PyPSA": "#2ca02c",
    "pandapower": "#ff7f0e",
    "PYPOWER": "#d62728",
    "PowerModels": "#8c564b",
    "ESFEX": "#9467bd",
    "GridCal": "#e377c2",
    "Egret": "#17becf",
    "MATPOWER": "#bcbd22",
}

SOLVER_MARKERS = {
    "scipy": "o",
    "PyPSA": "s",
    "pandapower": "^",
    "PYPOWER": "X",
    "PowerModels": "P",
    "ESFEX": "D",
    "GridCal": "p",
    "Egret": "h",
    "MATPOWER": "8",
}

# Display names for solver labels
SOLVER_DISPLAY = {
    "scipy": "scipy",
    "PyPSA": "PyPSA",
    "pandapower": "pandapwr",
    "PYPOWER": "PYPOWER",
    "PowerModels": "PwrModels",
    "ESFEX": "ESFEX",
    "GridCal": "GridCal",
    "Egret": "Egret",
    "MATPOWER": "MATPOWER",
}


def _display(solver_name: str) -> str:
    """Return display label for a solver."""
    return SOLVER_DISPLAY.get(solver_name, solver_name)


def _short_name(sys_name: str) -> str:
    """Short legend label for a system name (e.g. '9B', 'P1354B')."""
    return sys_name.replace("IEEE ", "").replace("PEGASE ", "P").replace("-bus", "B")


def _setup_paper_style():
    """Configure matplotlib for paper-ready output."""
    plt.rcParams.update({
        "font.family": "serif",
        "font.size": 14,
        "axes.titlesize": 18,
        "axes.titleweight": "bold",
        "axes.labelsize": 18,
        "axes.labelweight": "bold",
        "xtick.labelsize": 14,
        "ytick.labelsize": 14,
        "legend.fontsize": 14,
        "legend.framealpha": 0.9,
        "legend.edgecolor": "0.8",
        "figure.figsize": (14, 6),
        "axes.spines.top": False,
        "axes.spines.right": False,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.15,
    })


def warmup_julia():
    """Trigger Julia JIT compilation with a trivial 2-bus system.

    Warms up ESFEX DCOPF and (if available) PowerModels.jl so that all
    subsequent solve times reflect actual computation, not one-time JIT
    overhead (~17 s for ESFEX, ~30 s for PowerModels).
    """
    from esfex.bridge.julia_setup import get_esfex_module
    ESFEX = get_esfex_module()
    ESFEX.solve_dcopf(
        num_buses=2,
        demand=np.array([0.0, 100.0]),
        gen_bus=np.array([1], dtype=np.int64),
        gen_cost=np.array([10.0]),
        gen_max=np.array([200.0]),
        line_from=np.array([1], dtype=np.int64),
        line_to=np.array([2], dtype=np.int64),
        line_x=np.array([0.1]),
        line_cap=np.array([200.0]),
        slack_bus=1,
        base_impedance=100.0,
    )

    # Warm up PowerModels.jl if available
    try:
        from tests.fixtures.ieee_bus_data import ieee_9bus
        from tests.fixtures.ieee_reference_solvers import solve_with_powermodels
        solve_with_powermodels(ieee_9bus())
    except Exception:
        pass  # PowerModels not installed or not available


def _get_rss_mb() -> float:
    """Get current process RSS in MB."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / (1024 * 1024)
    except ImportError:
        try:
            with open('/proc/self/status') as f:
                for line in f:
                    if line.startswith('VmRSS:'):
                        return int(line.split()[1]) / 1024
        except (OSError, ValueError):
            pass
    return 0.0


class _PeakMemoryTracker:
    """Track peak memory via a background thread that samples RSS.

    A daemon thread polls ``/proc/self/status`` (VmRSS) or psutil at a
    configurable interval and records the maximum.  The delta between
    the baseline RSS at ``start()`` and the observed peak gives the
    additional memory consumed by the solver — even for non-Python
    allocations (e.g. Julia, C libraries).

    This avoids the ``ru_maxrss`` limitation where the high-water mark
    never decreases, causing later solvers to report 0 MB.

    Usage::

        tracker = _PeakMemoryTracker()
        tracker.start()
        # ... run solver ...
        peak_mb = tracker.stop()
    """

    def __init__(self, interval: float = 0.05):
        self._interval = interval
        self._rss0 = 0.0
        self._peak_rss = 0.0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _sampler(self):
        while not self._stop_event.is_set():
            rss = _get_rss_mb()
            if rss > self._peak_rss:
                self._peak_rss = rss
            self._stop_event.wait(self._interval)

    def start(self):
        gc.collect()
        self._rss0 = _get_rss_mb()
        self._peak_rss = self._rss0
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._sampler, daemon=True)
        self._thread.start()

    def stop(self) -> float:
        """Return peak additional memory (MB) since start()."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        # One final sample
        rss_now = _get_rss_mb()
        if rss_now > self._peak_rss:
            self._peak_rss = rss_now
        return max(0.0, self._peak_rss - self._rss0)


def _solve_ieee_system(ieee_data: dict, n_reps: int = 5) -> dict:
    """Solve an IEEE system with all solvers sequentially (N reps each).

    Returns dict with keys: 'name', 'ieee_data', 'solve_times',
    'solve_times_all', 'solve_times_iqr', 'solver_times', 'build_times',
    plus one key per solver ('PYPOWER', 'ESFEX', 'PyPSA', etc.).
    """
    name = ieee_data["name"]
    n = ieee_data["num_buses"]
    print(f"  Solving {name} ({n} buses, {len(ieee_data['lines'])} lines, "
          f"{n_reps} reps)...", flush=True)

    solve_times_all: dict[str, list] = {}
    solver_times_all: dict[str, list] = {}
    peak_memory: dict[str, float] = {}
    result: dict = {
        "name": name,
        "ieee_data": ieee_data,
        "solve_times": {},
        "solve_times_all": solve_times_all,
        "solve_times_iqr": {},
        "solver_times": {},
        "build_times": {},
        "peak_memory": peak_memory,
    }

    def _record(solver_name, dt_list, st_list, mem):
        solve_times_all[solver_name] = dt_list
        solver_times_all[solver_name] = st_list
        med = float(np.median(dt_list))
        med_s = float(np.median(st_list))
        result["solve_times"][solver_name] = med
        result["solve_times_iqr"][solver_name] = (
            float(np.percentile(dt_list, 25)),
            float(np.percentile(dt_list, 75)),
        )
        result["solver_times"][solver_name] = med_s
        result["build_times"][solver_name] = max(0.0, med - med_s)
        peak_memory[solver_name] = mem

    # PYPOWER (MATPOWER) reference
    from tests.fixtures.ieee_reference_solvers import solve_with_pypower
    dt_list, st_list = [], []
    ref_result = None
    _mem = _PeakMemoryTracker()
    _mem.start()
    for rep in range(n_reps):
        t0 = time.perf_counter()
        rep_result = solve_with_pypower(ieee_data)
        dt_list.append(time.perf_counter() - t0)
        st_list.append(rep_result.get("_solver_time", dt_list[-1]))
        if ref_result is None:
            ref_result = rep_result
    _record("PYPOWER", dt_list, st_list, _mem.stop())
    result["PYPOWER"] = ref_result

    # scipy (now a regular solver, no longer the reference)
    dt_list, st_list = [], []
    scipy_result = None
    _mem = _PeakMemoryTracker()
    _mem.start()
    for rep in range(n_reps):
        t0 = time.perf_counter()
        rep_result = compute_dc_opf_reference(ieee_data)
        dt_list.append(time.perf_counter() - t0)
        st_list.append(rep_result.get("_solver_time", dt_list[-1]))
        if scipy_result is None:
            scipy_result = rep_result
    _record("scipy", dt_list, st_list, _mem.stop())
    result["scipy"] = scipy_result

    # ESFEX (lightweight DCOPF)
    from esfex.bridge.julia_setup import get_esfex_module
    ESFEX = get_esfex_module()
    dt_list, st_list = [], []
    rfx_result = None
    _mem = _PeakMemoryTracker()
    _mem.start()
    for rep in range(n_reps):
        t0 = time.perf_counter()
        jl_result = ESFEX.solve_dcopf(
            num_buses=n,
            demand=np.array([b["pd_mw"] for b in ieee_data["buses"]], dtype=np.float64),
            gen_bus=np.array([g["bus"] + 1 for g in ieee_data["generators"]], dtype=np.int64),
            gen_cost=np.array([g["cost_mwh"] for g in ieee_data["generators"]], dtype=np.float64),
            gen_max=np.array([g["pg_max"] for g in ieee_data["generators"]], dtype=np.float64),
            gen_min=np.array([g.get("pg_min", 0.0) for g in ieee_data["generators"]], dtype=np.float64),
            line_from=np.array([l["from"] + 1 for l in ieee_data["lines"]], dtype=np.int64),
            line_to=np.array([l["to"] + 1 for l in ieee_data["lines"]], dtype=np.int64),
            line_x=np.array([l["x_pu"] for l in ieee_data["lines"]], dtype=np.float64),
            line_cap=np.array([l["rate_mw"] for l in ieee_data["lines"]], dtype=np.float64),
            line_tap=np.array([l.get("tap", 1.0) for l in ieee_data["lines"]], dtype=np.float64),
            line_shift=np.array([l.get("shift_deg", 0.0) for l in ieee_data["lines"]], dtype=np.float64),
            slack_bus=ieee_data["slack_bus"] + 1,
            base_impedance=float(ieee_data["base_mva"]),
        )
        dt_list.append(time.perf_counter() - t0)
        st_list.append(float(jl_result.get("_solver_time", dt_list[-1])))
        if rfx_result is None:
            rfx_result = {
                "status": str(jl_result["status"]),
                "total_cost": float(jl_result["total_cost"]),
                "angles_deg": list(jl_result["angles_deg"]),
                "line_flows_mw": list(jl_result["line_flows_mw"]),
                "gen_dispatch_list": list(jl_result["gen_dispatch_list"]),
                "gen_dispatch_mw": {int(k): float(v) for k, v in dict(jl_result["gen_dispatch_mw"]).items()},
            }
    _record("ESFEX", dt_list, st_list, _mem.stop())
    result["ESFEX"] = rfx_result
    print(f"    {name} ESFEX: ${rfx_result['total_cost']:,.0f} "
          f"(median {result['solve_times']['ESFEX']:.3f}s)", flush=True)

    # External solvers (PYPOWER already solved as reference above)
    try:
        from tests.fixtures.ieee_reference_solvers import get_available_solvers
        for solver_name, solver_fn in get_available_solvers().items():
            if solver_name == "PYPOWER":
                continue
            try:
                dt_list, st_list = [], []
                ext_result = None
                _mem = _PeakMemoryTracker()
                _mem.start()
                for rep in range(n_reps):
                    t0 = time.perf_counter()
                    rep_result = solver_fn(ieee_data)
                    dt_list.append(time.perf_counter() - t0)
                    st_list.append(rep_result.get("_solver_time", dt_list[-1]))
                    if ext_result is None:
                        ext_result = rep_result
                    else:
                        del rep_result
                _record(solver_name, dt_list, st_list, _mem.stop())
                result[solver_name] = ext_result
                print(f"    {solver_name}: ${ext_result['total_cost']:,.0f} "
                      f"(median {result['solve_times'][solver_name]:.3f}s)",
                      flush=True)
            except Exception as e:
                print(f"    {solver_name}: FAILED — {e}", flush=True)
    except ImportError:
        pass

    return result


def _solve_worker(ieee_data: dict) -> dict:
    """Multiprocessing worker — solve one IEEE system in a separate process.

    Each worker initialises its own Julia instance. This is the function
    called by ``ProcessPoolExecutor.map()``.
    """
    _ensure_sys_path()
    return _solve_ieee_system(ieee_data)


def _ensure_sys_path():
    """Ensure project root and src are on sys.path (for multiprocessing workers)."""
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    src = str(Path(root) / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _safe_print(msg: str) -> None:
    """Print that silently ignores BlockingIOError in multiprocessing workers."""
    try:
        print(msg, flush=True)
    except BlockingIOError:
        pass


def _solve_single_task(args: tuple) -> tuple:
    """Solve a single (system, solver) pair in its own process with N reps.

    Parameters
    ----------
    args : tuple
        (ieee_data, solver_name, n_reps)

    Returns
    -------
    tuple
        (system_name, solver_name, result_dict, dt_list, mem_mb, solver_time_list)
    """
    _ensure_sys_path()

    ieee_data, solver_name, n_reps = args
    name = ieee_data["name"]

    try:
        # Resolve solver function once
        if solver_name == "PYPOWER":
            from tests.fixtures.ieee_reference_solvers import solve_with_pypower
            solver_fn = solve_with_pypower
        elif solver_name == "scipy":
            solver_fn = compute_dc_opf_reference
        else:
            from tests.fixtures.ieee_reference_solvers import get_available_solvers
            solver_fn = get_available_solvers()[solver_name]

        dt_list = []
        solver_time_list = []
        result = None
        _mem = _PeakMemoryTracker()
        _mem.start()
        for rep in range(n_reps):
            t0 = time.perf_counter()
            rep_result = solver_fn(ieee_data)
            dt = time.perf_counter() - t0
            dt_list.append(dt)
            solver_time_list.append(rep_result.get("_solver_time", dt))
            if result is None:
                result = rep_result
            else:
                del rep_result  # Free memory from non-first reps

        mem_mb = _mem.stop()
        median_t = float(np.median(dt_list))
        _safe_print(f"    {name} / {solver_name}: median {median_t:.3f}s "
                    f"({n_reps} reps, {mem_mb:.1f}MB)")
        return (name, solver_name, result, dt_list, mem_mb, solver_time_list)

    except Exception as e:
        _safe_print(f"    {name} / {solver_name}: FAILED — {e}")
        return (name, solver_name, None, [0.0], 0.0, [0.0])


def _assemble_results(
    task_outputs: list[tuple],
    systems: list[dict],
) -> list[dict]:
    """Reassemble per-task outputs into per-system result dicts.

    Produces the same format as ``_solve_ieee_system()`` so all plotting
    functions work unchanged.
    """
    by_name: dict[str, dict] = {}
    for ieee_data in systems:
        n = ieee_data["name"]
        by_name[n] = {
            "name": n,
            "ieee_data": ieee_data,
            "solve_times": {},
            "solve_times_all": {},
            "solve_times_iqr": {},
            "solver_times": {},
            "build_times": {},
            "peak_memory": {},
        }

    for sys_name, solver_name, result, dt_list, mem_mb, solver_time_list in task_outputs:
        entry = by_name[sys_name]

        median_t = float(np.median(dt_list))
        median_s = float(np.median(solver_time_list))
        entry["solve_times"][solver_name] = median_t
        entry["solve_times_all"][solver_name] = dt_list
        entry["solve_times_iqr"][solver_name] = (
            float(np.percentile(dt_list, 25)),
            float(np.percentile(dt_list, 75)),
        )
        entry["solver_times"][solver_name] = median_s
        entry["build_times"][solver_name] = max(0.0, median_t - median_s)
        entry["peak_memory"][solver_name] = mem_mb

        if result is not None:
            entry[solver_name] = result

    # Return in same order as input systems
    return [by_name[s["name"]] for s in systems]


def _build_task_list(systems: list[dict], n_reps: int = 5) -> list[tuple]:
    """Build list of (ieee_data, solver_name, n_reps) tasks for parallel execution.

    Julia-based solvers (ESFEX, PowerModels) are EXCLUDED because Julia
    uses fork-unsafe threads.  The caller should solve them in the main
    process via ``_solve_julia_all()`` before spawning the parallel pool.
    """
    # Julia-based solvers that must run in the main process
    _JULIA_SOLVERS = {"PowerModels"}

    solver_names = ["PYPOWER", "scipy"]

    # Add available external solvers (Python-only)
    try:
        from tests.fixtures.ieee_reference_solvers import get_available_solvers
        for name in get_available_solvers():
            if name not in _JULIA_SOLVERS and name != "PYPOWER":
                solver_names.append(name)
    except ImportError:
        pass

    tasks = []
    for ieee_data in systems:
        for solver_name in solver_names:
            tasks.append((ieee_data, solver_name, n_reps))
    return tasks


def _solve_julia_all(systems: list[dict], n_reps: int = 5) -> list[tuple]:
    """Solve all systems with Julia-based solvers in the main process (N reps).

    Julia is fork-unsafe, so ESFEX and PowerModels must run here
    (not in the multiprocessing pool).  JIT is pre-warmed via
    ``warmup_julia()``.

    Returns list of (name, solver, result, dt_list, mem_mb, solver_time_list).
    """
    from esfex.bridge.julia_setup import get_esfex_module
    ESFEX = get_esfex_module()

    # Check if PowerModels is available
    pm_available = False
    try:
        from tests.fixtures.ieee_reference_solvers import solve_with_powermodels
        pm_available = True
    except ImportError:
        pass

    outputs = []
    for ieee_data in systems:
        name = ieee_data["name"]
        n = ieee_data["num_buses"]

        # ── ESFEX ──
        dt_list, st_list = [], []
        rfx_result = None
        _mem = _PeakMemoryTracker()
        _mem.start()
        for rep in range(n_reps):
            t0 = time.perf_counter()
            jl_result = ESFEX.solve_dcopf(
                num_buses=n,
                demand=np.array([b["pd_mw"] for b in ieee_data["buses"]], dtype=np.float64),
                gen_bus=np.array([g["bus"] + 1 for g in ieee_data["generators"]], dtype=np.int64),
                gen_cost=np.array([g["cost_mwh"] for g in ieee_data["generators"]], dtype=np.float64),
                gen_max=np.array([g["pg_max"] for g in ieee_data["generators"]], dtype=np.float64),
                gen_min=np.array([g.get("pg_min", 0.0) for g in ieee_data["generators"]], dtype=np.float64),
                line_from=np.array([l["from"] + 1 for l in ieee_data["lines"]], dtype=np.int64),
                line_to=np.array([l["to"] + 1 for l in ieee_data["lines"]], dtype=np.int64),
                line_x=np.array([l["x_pu"] for l in ieee_data["lines"]], dtype=np.float64),
                line_cap=np.array([l["rate_mw"] for l in ieee_data["lines"]], dtype=np.float64),
                line_tap=np.array([l.get("tap", 1.0) for l in ieee_data["lines"]], dtype=np.float64),
                line_shift=np.array([l.get("shift_deg", 0.0) for l in ieee_data["lines"]], dtype=np.float64),
                slack_bus=ieee_data["slack_bus"] + 1,
                base_impedance=float(ieee_data["base_mva"]),
            )
            dt_list.append(time.perf_counter() - t0)
            st_list.append(float(jl_result.get("_solver_time", dt_list[-1])))
            if rfx_result is None:
                rfx_result = {
                    "status": str(jl_result["status"]),
                    "total_cost": float(jl_result["total_cost"]),
                    "angles_deg": list(jl_result["angles_deg"]),
                    "line_flows_mw": list(jl_result["line_flows_mw"]),
                    "gen_dispatch_list": list(jl_result["gen_dispatch_list"]),
                    "gen_dispatch_mw": {int(k): float(v) for k, v in dict(jl_result["gen_dispatch_mw"]).items()},
                }
        mem_mb = _mem.stop()
        med = float(np.median(dt_list))
        print(f"    {name} / ESFEX: median {med:.3f}s ({n_reps} reps, {mem_mb:.1f}MB)",
              flush=True)
        outputs.append((name, "ESFEX", rfx_result, dt_list, mem_mb, st_list))

        # ── PowerModels ──
        if pm_available:
            dt_list, st_list = [], []
            pm_result = None
            try:
                _mem = _PeakMemoryTracker()
                _mem.start()
                for rep in range(n_reps):
                    t0 = time.perf_counter()
                    rep_result = solve_with_powermodels(ieee_data)
                    dt_list.append(time.perf_counter() - t0)
                    st_list.append(rep_result.get("_solver_time", dt_list[-1]))
                    if pm_result is None:
                        pm_result = rep_result
                mem_mb = _mem.stop()
                med = float(np.median(dt_list))
                print(f"    {name} / PowerModels: median {med:.3f}s "
                      f"({n_reps} reps, {mem_mb:.1f}MB)", flush=True)
                outputs.append((name, "PowerModels", pm_result, dt_list, mem_mb, st_list))
            except Exception as e:
                print(f"    {name} / PowerModels: FAILED — {e}", flush=True)

    return outputs


def _get_solver_names(results: list[dict]) -> list[str]:
    """Get list of solver names present in any result (including PYPOWER)."""
    solver_names = []
    for name in SOLVER_COLORS:
        if any(name in r for r in results):
            solver_names.append(name)
    return solver_names


def _add_figure_legend(fig, solvers: list[str], systems: list[str],
                       has_ac: bool = False, ncol: int = 10):
    """Add a unified legend centred below both subplots.

    Row 1: system colour patches (primary encoding).
    Row 2: solver marker proxies in grey (secondary encoding).
    When *has_ac* is True, appends filled-circle "DC" / hollow-circle "AC"
    handles after the solver markers.
    """
    handles = []
    for sys_name in systems:
        color = SYSTEM_COLORS.get(sys_name, "#999")
        handles.append(Patch(facecolor=color, edgecolor="none",
                             label=_short_name(sys_name)))
    for sname in solvers:
        marker = SOLVER_MARKERS.get(sname, "o")
        handles.append(Line2D(
            [], [], marker=marker, color="none", markerfacecolor="0.5",
            markeredgecolor="white", markersize=9, label=_display(sname),
        ))
    if has_ac:
        handles.append(Line2D(
            [], [], marker="<", color="none", markerfacecolor="0.5",
            markeredgecolor="white", markersize=9, label="DC",
        ))
        handles.append(Line2D(
            [], [], marker=">", color="none", markerfacecolor="none",
            markeredgecolor="0.5", markeredgewidth=1.2, markersize=9,
            label="AC",
        ))
    fig.legend(
        handles=handles, loc="upper center",
        bbox_to_anchor=(0.5, 1.0),
        ncol=ncol, fontsize=14,
        columnspacing=1.0, handletextpad=0.3,
        frameon=True, framealpha=0.9, edgecolor="0.8",
    )


def _wrap_angles_deg(arr: np.ndarray) -> np.ndarray:
    """Wrap angles to [-180, 180] degrees."""
    return (arr + 180.0) % 360.0 - 180.0


def _wrap_results_angles(results: list[dict]) -> list[dict]:
    """Return shallow copy of results with all angles_deg wrapped to [-180, 180].

    Does NOT modify the original dicts.
    """
    wrapped = []
    for r in results:
        r2 = dict(r)
        for key in list(r2):
            if isinstance(r2[key], dict) and "angles_deg" in r2[key]:
                r2[key] = dict(r2[key])
                raw = np.asarray(r2[key]["angles_deg"], dtype=float)
                r2[key]["angles_deg"] = _wrap_angles_deg(raw).tolist()
        wrapped.append(r2)
    return wrapped


def _gen_dispatch_per_bus(sol_dict: dict, n_buses: int) -> np.ndarray:
    """Convert gen_dispatch_mw {bus_idx: MW} to a per-bus array of length n_buses."""
    arr = np.zeros(n_buses)
    for bus, mw in sol_dict.get("gen_dispatch_mw", {}).items():
        bus = int(bus)
        if 0 <= bus < n_buses:
            arr[bus] += mw  # += handles multiple gens at same bus
    return arr


def _dual_scatter(ax, results: list[dict], solvers: list[str], key: str):
    """Plot a dual-encoded parity scatter on *ax* (solver vs consensus median).

    Primary encoding: **color** = IEEE system (highly visible).
    Secondary encoding: **marker shape** = solver.

    X-axis = element-wise median consensus across all converged solvers.
    Y-axis = individual solver values.

    Returns (all_ref, all_sol) flat lists for axis-limit computation.
    """
    consensus = _compute_consensus(results, key, solvers)

    all_ref: list[float] = []
    all_sol: list[float] = []

    # Compute data range for proportional solver offsets
    all_vals = []
    for r in results:
        c = consensus.get(r["name"])
        if c is not None:
            all_vals.extend(c)
        for sname in solvers:
            d = r.get(sname)
            if isinstance(d, dict) and key in d:
                all_vals.extend(np.asarray(d[key], dtype=float))
    data_range = (max(all_vals) - min(all_vals)) if all_vals else 1.0
    offset_step = data_range * 0.008

    # Draw large systems first (background), small ones on top
    sorted_results = sorted(results,
                            key=lambda r: r["ieee_data"]["num_buses"],
                            reverse=True)
    for z_idx, r in enumerate(sorted_results):
        sys_name = r["name"]
        sys_color = SYSTEM_COLORS.get(sys_name, "#999")
        ref_vals = consensus.get(sys_name)
        if ref_vals is None:
            continue
        for s_idx, sname in enumerate(solvers):
            d = r.get(sname)
            if not isinstance(d, dict) or key not in d:
                continue
            marker = SOLVER_MARKERS.get(sname, "o")
            sol_vals = np.asarray(d[key], dtype=float)
            n = min(len(ref_vals), len(sol_vals))
            idx = _sample_parity_idx(ref_vals[:n], sol_vals[:n])
            dx = (s_idx - (len(solvers) - 1) / 2) * offset_step
            ax.scatter(
                ref_vals[idx] + dx, sol_vals[idx],
                c=sys_color, marker=marker,
                s=55, alpha=0.8, edgecolors="white", linewidths=0.4,
                zorder=3 + z_idx,
            )
            all_ref.extend(ref_vals[:n])
            all_sol.extend(sol_vals[:n])

    return all_ref, all_sol


def _add_parity_line(ax, all_ref, all_sol):
    """Add dashed 1:1 reference line and set equal limits."""
    if not all_ref:
        return
    ref_arr = np.asarray(all_ref, dtype=float)
    sol_arr = np.asarray(all_sol, dtype=float)
    mask = np.isfinite(ref_arr) & np.isfinite(sol_arr)
    if not mask.any():
        return
    vmin = min(ref_arr[mask].min(), sol_arr[mask].min())
    vmax = max(ref_arr[mask].max(), sol_arr[mask].max())
    margin = (vmax - vmin) * 0.08
    lims = [vmin - margin, vmax + margin]
    ax.plot(lims, lims, "--", color="0.5", linewidth=1, zorder=1)
    ax.set_xlim(lims)
    ax.set_ylim(lims)
    ax.set_aspect("equal", adjustable="box")


def _sample_extremes(arr: np.ndarray, max_points: int = 100) -> np.ndarray:
    """Sample *max_points* from *arr*: 50 % extremes, 50 % middle values.

    Returns the sampled array (unordered).  If ``len(arr) <= max_points``,
    the full array is returned unchanged.
    """
    n = len(arr)
    if n <= max_points:
        return arr
    n_extremes = max_points // 2          # 50 from tails (25 low + 25 high)
    n_middle = max_points - n_extremes    # 50 from centre
    order = np.argsort(arr)
    n_lo = n_extremes // 2
    n_hi = n_extremes - n_lo
    lo_idx = order[:n_lo]
    hi_idx = order[-n_hi:]
    mid_pool = order[n_lo: n - n_hi]
    rng = np.random.default_rng(42)       # deterministic for reproducibility
    mid_idx = rng.choice(mid_pool, size=min(n_middle, len(mid_pool)), replace=False)
    return arr[np.concatenate([lo_idx, hi_idx, mid_idx])]


def _sample_parity_idx(
    ref: np.ndarray, sol: np.ndarray, max_points: int = 200,
) -> np.ndarray:
    """Return indices that preserve parity-plot extremes.

    Samples by ``|sol - ref|`` so that both the best- and worst-matching
    points are kept, plus a random middle sample.  Both *ref* and *sol*
    can then be indexed with the result to keep (x, y) pairs aligned.
    """
    n = len(ref)
    if n <= max_points:
        return np.arange(n)
    err = np.abs(sol - ref)
    n_extremes = max_points // 2
    n_middle = max_points - n_extremes
    order = np.argsort(err)
    n_lo = n_extremes // 2
    n_hi = n_extremes - n_lo
    lo_idx = order[:n_lo]
    hi_idx = order[-n_hi:]
    mid_pool = order[n_lo: n - n_hi]
    rng = np.random.default_rng(42)
    mid_idx = rng.choice(mid_pool, size=min(n_middle, len(mid_pool)), replace=False)
    return np.concatenate([lo_idx, hi_idx, mid_idx])


def _compute_consensus(
    results: list[dict],
    key: str,
    solvers: list[str] | None = None,
) -> dict[str, np.ndarray | None]:
    """Element-wise median consensus for *key* across all converged solvers.

    Returns ``{system_name: consensus_array}`` (or ``None`` when no solver
    produced the quantity for that system).
    """
    consensus: dict[str, np.ndarray | None] = {}
    solver_pool = solvers or list(SOLVER_COLORS.keys())
    for r in results:
        arrays: list[np.ndarray] = []
        for sname in solver_pool:
            d = r.get(sname)
            if isinstance(d, dict) and key in d:
                arrays.append(np.asarray(d[key], dtype=float))
        if arrays:
            min_len = min(len(a) for a in arrays)
            stacked = np.stack([a[:min_len] for a in arrays])
            consensus[r["name"]] = np.median(stacked, axis=0)
        else:
            consensus[r["name"]] = None
    return consensus


def _compute_consensus_cost(
    results: list[dict],
) -> dict[str, float | None]:
    """Median total cost across all converged solvers, per system."""
    consensus: dict[str, float | None] = {}
    for r in results:
        costs: list[float] = []
        for sname in SOLVER_COLORS:
            d = r.get(sname)
            if isinstance(d, dict) and "total_cost" in d:
                costs.append(float(d["total_cost"]))
        consensus[r["name"]] = float(np.median(costs)) if costs else None
    return consensus


def _compute_consensus_dispatch(
    results: list[dict],
    solvers: list[str] | None = None,
) -> dict[str, np.ndarray | None]:
    """Element-wise median of per-bus generation dispatch across solvers."""
    consensus: dict[str, np.ndarray | None] = {}
    solver_pool = solvers or list(SOLVER_COLORS.keys())
    for r in results:
        n_buses = r["ieee_data"]["num_buses"]
        arrays: list[np.ndarray] = []
        for sname in solver_pool:
            d = r.get(sname)
            if isinstance(d, dict) and "gen_dispatch_mw" in d:
                arrays.append(_gen_dispatch_per_bus(d, n_buses))
        if arrays:
            stacked = np.stack(arrays)
            consensus[r["name"]] = np.median(stacked, axis=0)
        else:
            consensus[r["name"]] = None
    return consensus


def _add_log_violins(ax, solver_errors, positions, color="0.60",
                     alpha=0.55, width=0.7):
    """Overlay right half-violin plots on a log-scaled error axis.

    Violins are drawn on the right side of each position so that scatter
    points on the left side remain visible.

    Parameters
    ----------
    ax : matplotlib Axes (yscale already set to "log")
    solver_errors : list[list[float]], one array of positive errors per solver
    positions : array-like x-positions (one per solver)
    """
    from scipy.stats import gaussian_kde

    for pos, errs in zip(positions, solver_errors):
        errs = np.asarray(errs, dtype=np.float64)
        errs = errs[errs > 0]
        if len(errs) < 3:
            continue
        log_errs = np.log10(errs)
        try:
            kde = gaussian_kde(log_errs, bw_method="scott")
        except Exception:
            continue

        lo, hi = log_errs.min() - 0.3, log_errs.max() + 0.3
        y_log = np.linspace(lo, hi, 200)
        density = kde(y_log)
        peak = density.max()
        if peak <= 0:
            continue
        density = density / peak * (width / 2)

        y_vals = 10 ** y_log
        ax.fill_betweenx(y_vals, pos, pos + density,
                         facecolor=color, edgecolor="0.55",
                         linewidth=0.5, alpha=alpha, zorder=1)
        # Median line (right half only)
        med_log = float(np.median(log_errs))
        med_dens = float(np.atleast_1d(kde(med_log))[0]) / peak * (width / 2)
        ax.plot([pos, pos + med_dens],
                [10 ** med_log, 10 ** med_log],
                color="0.3", linewidth=1.5, solid_capstyle="round", zorder=2)


# ── Figure 1: Voltage Angles ────────────────────────────────────────────


def _get_ac_solvers(acopf_results):
    """Return list of all converged AC solver names."""
    if not acopf_results:
        return []
    ac_all = set()
    for r in acopf_results:
        for s in SOLVER_COLORS:
            d = r.get(s)
            if isinstance(d, dict) and "OPTIMAL" in d.get("status", ""):
                ac_all.add(s)
    return [s for s in SOLVER_COLORS if s in ac_all]


def plot_voltage_angles(results: list[dict], output_dir: Path,
                        acopf_results: list[dict] | None = None):
    """Figure 1: Voltage angle parity (dual-encoded) + error box plots.

    When *acopf_results* is provided, AC data is overlaid:
      (a) hollow markers for AC parity (with marginal histograms),
      (b) solver columns split left=AC / right=DC.
    """
    from matplotlib.gridspec import GridSpec

    # Wrap all angles to [-180, 180] (physically meaningful range)
    results = _wrap_results_angles(results)
    if acopf_results is not None:
        acopf_results = _wrap_results_angles(acopf_results)

    solvers = _get_solver_names(results)
    system_names = [r["name"] for r in results]
    has_ac = acopf_results is not None and len(acopf_results) > 0
    ac_solvers = _get_ac_solvers(acopf_results) if has_ac else []

    fig = plt.figure(figsize=(16, 8))
    # Outer grid: left half = parity with histograms, right half = deviation
    outer = GridSpec(1, 2, figure=fig, wspace=0.30)

    # Left sub-grid: 2×2 for scatter + marginal histograms
    inner_left = outer[0].subgridspec(2, 2, width_ratios=[4, 1],
                                       height_ratios=[1, 4],
                                       hspace=0.05, wspace=0.10)
    ax1 = fig.add_subplot(inner_left[1, 0])       # main scatter
    ax_histx = fig.add_subplot(inner_left[0, 0], sharex=ax1)  # top histogram
    ax_histy = fig.add_subplot(inner_left[1, 1], sharey=ax1)  # right histogram
    ax_corner = fig.add_subplot(inner_left[0, 1])  # empty corner
    ax_corner.axis("off")

    # Right panel: deviation strip plot
    ax2 = fig.add_subplot(outer[1])

    # ── (a) Parity scatter: one point per bus per (system, solver) ──
    all_ref, all_sol = _dual_scatter(ax1, results, solvers, "angles_deg")

    # AC overlay: hollow markers vs AC consensus
    if has_ac and ac_solvers:
        ac_consensus = _compute_consensus(acopf_results, "angles_deg", ac_solvers)
        for r_ac in acopf_results:
            sys_name = r_ac["name"]
            ref_vals = ac_consensus.get(sys_name)
            if ref_vals is None:
                continue
            sys_color = SYSTEM_COLORS.get(sys_name, "#999")
            for sname in ac_solvers:
                d = r_ac.get(sname)
                if not isinstance(d, dict) or "angles_deg" not in d:
                    continue
                if "OPTIMAL" not in d.get("status", ""):
                    continue
                marker = SOLVER_MARKERS.get(sname, "o")
                sol_vals = np.asarray(d["angles_deg"], dtype=float)
                n = min(len(ref_vals), len(sol_vals))
                idx = _sample_parity_idx(ref_vals[:n], sol_vals[:n])
                ax1.scatter(
                    ref_vals[idx], sol_vals[idx],
                    facecolors="none", edgecolors=sys_color,
                    marker=marker, s=55, alpha=0.6, linewidths=1.2,
                    zorder=6,
                )
                all_ref.extend(ref_vals[:n])
                all_sol.extend(sol_vals[:n])

    _add_parity_line(ax1, all_ref, all_sol)
    ax1.set_aspect("auto")  # override equal aspect (incompatible with shared axes)
    ax1.grid(True, alpha=0.3, linestyle="--")
    ax1.set_xlabel("Consensus Angle (deg)")
    ax1.set_ylabel("Solver Angle (deg)")
    ax_histx.set_title("(a) Voltage Angle Parity")

    # ── Marginal histograms ──
    ref_arr = np.asarray(all_ref, dtype=float)
    sol_arr = np.asarray(all_sol, dtype=float)
    mask = np.isfinite(ref_arr) & np.isfinite(sol_arr)
    if mask.any():
        n_bins = 60
        ax_histx.hist(ref_arr[mask], bins=n_bins, color="#5a7d9a",
                       alpha=0.6, edgecolor="none", density=False, log=True)
        ax_histy.hist(sol_arr[mask], bins=n_bins, orientation="horizontal",
                       color="#5a7d9a", alpha=0.6, edgecolor="none", density=False, log=True)
    ax_histx.axis("off")
    ax_histy.axis("off")

    # ── (b) |deviation from consensus| on log scale ──
    dc_consensus = _compute_consensus(results, "angles_deg", solvers)
    ac_consensus_b = _compute_consensus(acopf_results, "angles_deg", ac_solvers) if has_ac else {}

    all_plot_solvers = list(solvers)
    for s in ac_solvers:
        if s not in all_plot_solvers:
            all_plot_solvers.append(s)

    positions = np.arange(len(all_plot_solvers))
    violin_data = [[] for _ in all_plot_solvers]
    dc_x = -0.15 if has_ac else -0.20
    ac_x = -0.30
    for s_idx, sname in enumerate(all_plot_solvers):
        # DC deviations
        if sname in solvers:
            for r in results:
                d = r.get(sname)
                if not isinstance(d, dict) or "angles_deg" not in d:
                    continue
                ref = dc_consensus.get(r["name"])
                if ref is None:
                    continue
                sol = np.asarray(d["angles_deg"], dtype=float)
                n = min(len(ref), len(sol))
                abs_err = np.abs(sol[:n] - ref[:n])
                abs_err = np.where(abs_err < 1e-15, 1e-15, abs_err)
                violin_data[s_idx].extend(abs_err.tolist())
                sampled = _sample_extremes(abs_err)
                sys_color = SYSTEM_COLORS.get(r["name"], "#999")
                jitter = (list(SYSTEM_COLORS.keys()).index(r["name"])
                          - len(SYSTEM_COLORS) / 2) * 0.02
                ax2.scatter(
                    np.full_like(sampled, s_idx + dc_x + jitter), sampled,
                    c=sys_color, marker="<",
                    s=28, alpha=0.6, edgecolors="none", zorder=3,
                )
        # AC deviations
        if has_ac and sname in ac_solvers:
            for r_ac in acopf_results:
                d = r_ac.get(sname)
                if not isinstance(d, dict) or "angles_deg" not in d:
                    continue
                if "OPTIMAL" not in d.get("status", ""):
                    continue
                ref = ac_consensus_b.get(r_ac["name"])
                if ref is None:
                    continue
                sol = np.asarray(d["angles_deg"], dtype=float)
                n = min(len(ref), len(sol))
                abs_err = np.abs(sol[:n] - ref[:n])
                abs_err = np.where(abs_err < 1e-15, 1e-15, abs_err)
                violin_data[s_idx].extend(abs_err.tolist())
                sampled = _sample_extremes(abs_err)
                sys_color = SYSTEM_COLORS.get(r_ac["name"], "#999")
                jitter = (list(SYSTEM_COLORS.keys()).index(r_ac["name"])
                          - len(SYSTEM_COLORS) / 2) * 0.02
                ax2.scatter(
                    np.full_like(sampled, s_idx + ac_x + jitter), sampled,
                    facecolors="none", edgecolors=sys_color, marker=">",
                    s=28, alpha=0.6, linewidths=0.8, zorder=4,
                )

    ax2.set_yscale("log")
    _add_log_violins(ax2, violin_data, positions)
    ax2.set_xticks(positions)
    ax2.set_xticklabels([_display(s) for s in all_plot_solvers],
                        rotation=45, ha="right")
    ax2.set_ylabel("|Angle Deviation| (deg)")
    ax2.set_title("(b) Deviation from Consensus")
    for boundary in np.arange(0.5, len(all_plot_solvers) - 0.5, 1.0):
        ax2.axvline(boundary, color="0.65", linewidth=0.8, zorder=2)
    ax2.grid(axis="y", alpha=0.3, which="both")

    fig.subplots_adjust(left=0.06, right=0.97, top=0.83, bottom=0.06)
    _add_figure_legend(fig, all_plot_solvers, system_names, has_ac=has_ac)
    out_path = output_dir / "fig_voltage_angles.pdf"
    out_path_1 = output_dir / "fig_voltage_angles.png"
    out_path_2 = output_dir / "fig_voltage_angles.svg"
    fig.savefig(out_path, dpi=300)
    fig.savefig(out_path_1, dpi=300)
    fig.savefig(out_path_2, dpi=300)
    print(f"  Saved {out_path}")
    plt.close(fig)


# ── Figure 2: Line Power Flows ──────────────────────────────────────────


def plot_line_flows(results: list[dict], output_dir: Path,
                    acopf_results: list[dict] | None = None):
    """Figure 2: Line flow parity (dual-encoded) + flow error strip plot.

    AC overlay: hollow markers on parity, left/right split on error strips.
    """
    from matplotlib.gridspec import GridSpec

    solvers = _get_solver_names(results)
    system_names = [r["name"] for r in results]
    has_ac = acopf_results is not None and len(acopf_results) > 0
    ac_solvers = _get_ac_solvers(acopf_results) if has_ac else []

    fig = plt.figure(figsize=(16, 8))
    outer = GridSpec(1, 2, figure=fig, wspace=0.30)

    # Left sub-grid: scatter + marginal histograms
    inner_left = outer[0].subgridspec(2, 2, width_ratios=[4, 1],
                                       height_ratios=[1, 4],
                                       hspace=0.05, wspace=0.10)
    ax1 = fig.add_subplot(inner_left[1, 0])
    ax_histx = fig.add_subplot(inner_left[0, 0], sharex=ax1)
    ax_histy = fig.add_subplot(inner_left[1, 1], sharey=ax1)
    ax_corner = fig.add_subplot(inner_left[0, 1])
    ax_corner.axis("off")

    ax2 = fig.add_subplot(outer[1])

    # ── (a) Parity scatter ──
    all_ref, all_sol = _dual_scatter(ax1, results, solvers, "line_flows_mw")

    # AC overlay: hollow markers vs AC consensus
    if has_ac and ac_solvers:
        ac_consensus = _compute_consensus(acopf_results, "line_flows_mw", ac_solvers)
        for r_ac in acopf_results:
            sys_name = r_ac["name"]
            ref_vals = ac_consensus.get(sys_name)
            if ref_vals is None:
                continue
            sys_color = SYSTEM_COLORS.get(sys_name, "#999")
            for sname in ac_solvers:
                d = r_ac.get(sname)
                if not isinstance(d, dict) or "line_flows_mw" not in d:
                    continue
                if "OPTIMAL" not in d.get("status", ""):
                    continue
                marker = SOLVER_MARKERS.get(sname, "o")
                sol_vals = np.asarray(d["line_flows_mw"], dtype=float)
                n = min(len(ref_vals), len(sol_vals))
                idx = _sample_parity_idx(ref_vals[:n], sol_vals[:n])
                ax1.scatter(
                    ref_vals[idx], sol_vals[idx],
                    facecolors="none", edgecolors=sys_color,
                    marker=marker, s=55, alpha=0.6, linewidths=1.2,
                    zorder=6,
                )
                all_ref.extend(ref_vals[:n])
                all_sol.extend(sol_vals[:n])

    _add_parity_line(ax1, all_ref, all_sol)
    ax1.set_aspect("auto")  # override equal aspect (incompatible with shared axes)
    ax1.grid(True, alpha=0.3, linestyle="--")
    ax1.set_xlabel("Consensus Flow (MW)")
    ax1.set_ylabel("Solver Flow (MW)")
    ax_histx.set_title("(a) Line Flow Parity")

    # ── Marginal histograms ──
    ref_arr = np.asarray(all_ref, dtype=float)
    sol_arr = np.asarray(all_sol, dtype=float)
    mask = np.isfinite(ref_arr) & np.isfinite(sol_arr)
    if mask.any():
        n_bins = 60
        ax_histx.hist(ref_arr[mask], bins=n_bins, color="#5a7d9a",
                       alpha=0.6, edgecolor="none", density=False, log=True)
        ax_histy.hist(sol_arr[mask], bins=n_bins, orientation="horizontal",
                       color="#5a7d9a", alpha=0.6, edgecolor="none", density=False, log=True)
    ax_histx.axis("off")
    ax_histy.axis("off")

    # ── (b) |deviation from consensus| on log scale ──
    dc_consensus = _compute_consensus(results, "line_flows_mw", solvers)
    ac_consensus_b = _compute_consensus(acopf_results, "line_flows_mw", ac_solvers) if has_ac else {}

    all_plot_solvers = list(solvers)
    for s in ac_solvers:
        if s not in all_plot_solvers:
            all_plot_solvers.append(s)

    positions = np.arange(len(all_plot_solvers))
    violin_data = [[] for _ in all_plot_solvers]
    dc_x = -0.15 if has_ac else -0.20
    ac_x = -0.30
    for s_idx, sname in enumerate(all_plot_solvers):
        # DC deviations
        if sname in solvers:
            for r in results:
                d = r.get(sname)
                if not isinstance(d, dict) or "line_flows_mw" not in d:
                    continue
                ref = dc_consensus.get(r["name"])
                if ref is None:
                    continue
                sol = np.asarray(d["line_flows_mw"], dtype=float)
                n = min(len(ref), len(sol))
                abs_err = np.abs(sol[:n] - ref[:n])
                abs_err = np.where(abs_err < 1e-15, 1e-15, abs_err)
                violin_data[s_idx].extend(abs_err.tolist())
                sampled = _sample_extremes(abs_err)
                sys_color = SYSTEM_COLORS.get(r["name"], "#999")
                jitter = (list(SYSTEM_COLORS.keys()).index(r["name"])
                          - len(SYSTEM_COLORS) / 2) * 0.02
                ax2.scatter(
                    np.full_like(sampled, s_idx + dc_x + jitter), sampled,
                    c=sys_color, marker="<",
                    s=28, alpha=0.6, edgecolors="none", zorder=3,
                )
        # AC deviations
        if has_ac and sname in ac_solvers:
            for r_ac in acopf_results:
                d = r_ac.get(sname)
                if not isinstance(d, dict) or "line_flows_mw" not in d:
                    continue
                if "OPTIMAL" not in d.get("status", ""):
                    continue
                ref = ac_consensus_b.get(r_ac["name"])
                if ref is None:
                    continue
                sol = np.asarray(d["line_flows_mw"], dtype=float)
                n = min(len(ref), len(sol))
                abs_err = np.abs(sol[:n] - ref[:n])
                abs_err = np.where(abs_err < 1e-15, 1e-15, abs_err)
                violin_data[s_idx].extend(abs_err.tolist())
                sampled = _sample_extremes(abs_err)
                sys_color = SYSTEM_COLORS.get(r_ac["name"], "#999")
                jitter = (list(SYSTEM_COLORS.keys()).index(r_ac["name"])
                          - len(SYSTEM_COLORS) / 2) * 0.02
                ax2.scatter(
                    np.full_like(sampled, s_idx + ac_x + jitter), sampled,
                    facecolors="none", edgecolors=sys_color, marker=">",
                    s=28, alpha=0.6, linewidths=0.8, zorder=4,
                )

    ax2.set_yscale("log")
    _add_log_violins(ax2, violin_data, positions)
    ax2.set_xticks(positions)
    ax2.set_xticklabels([_display(s) for s in all_plot_solvers],
                        rotation=45, ha="right")
    ax2.set_ylabel("|Flow Deviation| (MW)")
    ax2.set_title("(b) Deviation from Consensus")
    for boundary in np.arange(0.5, len(all_plot_solvers) - 0.5, 1.0):
        ax2.axvline(boundary, color="0.65", linewidth=0.8, zorder=2)
    ax2.grid(axis="y", alpha=0.3, which="both")

    fig.subplots_adjust(left=0.06, right=0.97, top=0.83, bottom=0.06)
    _add_figure_legend(fig, all_plot_solvers, system_names, has_ac=has_ac)
    out_path = output_dir / "fig_line_flows.pdf"
    out_path_1 = output_dir / "fig_line_flows.png"
    out_path_2 = output_dir / "fig_line_flows.svg"
    fig.savefig(out_path, dpi=300)
    fig.savefig(out_path_1, dpi=300)
    fig.savefig(out_path_2, dpi=300)
    print(f"  Saved {out_path}")
    plt.close(fig)


# ── Figure 3: Generation Dispatch ────────────────────────────────────────


def plot_generation_dispatch(results: list[dict], output_dir: Path,
                             acopf_results: list[dict] | None = None):
    """Figure 3: Generation dispatch (2×2 layout).

    Left column (spans both rows): (a) dispatch parity scatter.
    Top-right:  (b) |dispatch error| per generator on log scale.
    Bottom-right: (c) |cost deviation| (%) on log scale.
    """
    solvers = _get_solver_names(results)
    system_names = [r["name"] for r in results]
    has_ac = acopf_results is not None and len(acopf_results) > 0
    ac_solvers = _get_ac_solvers(acopf_results) if has_ac else []

    fig = plt.figure(figsize=(16, 12))
    gs = fig.add_gridspec(2, 2, hspace=0.4, wspace=0.30,
                          left=0.07, right=0.97, top=0.9, bottom=0.06)

    # (a) parity with marginal histograms — left column
    inner_left = gs[:, 0].subgridspec(2, 2, width_ratios=[4, 1],
                                       height_ratios=[1, 4],
                                       hspace=0.05, wspace=0.10)
    ax1 = fig.add_subplot(inner_left[1, 0])
    ax_histx = fig.add_subplot(inner_left[0, 0], sharex=ax1)
    ax_histy = fig.add_subplot(inner_left[1, 1], sharey=ax1)
    ax_corner = fig.add_subplot(inner_left[0, 1])
    ax_corner.axis("off")

    ax_disp = fig.add_subplot(gs[0, 1])   # (b) dispatch error strip
    ax_cost = fig.add_subplot(gs[1, 1])   # (c) cost deviation strip

    # ── (a) Dispatch parity scatter — one point per BUS ──
    dc_disp_consensus = _compute_consensus_dispatch(results, solvers)
    all_ref: list[float] = []
    all_sol: list[float] = []
    sorted_results = sorted(results,
                            key=lambda r: r["ieee_data"]["num_buses"],
                            reverse=True)

    # Compute offset step from data range
    _all_vals = []
    for r in results:
        c = dc_disp_consensus.get(r["name"])
        if c is not None:
            _all_vals.extend(c)
    data_range = (max(_all_vals) - min(_all_vals)) if _all_vals else 1.0
    offset_step = data_range * 0.008

    for z_idx, r in enumerate(sorted_results):
        sys_name = r["name"]
        sys_color = SYSTEM_COLORS.get(sys_name, "#999")
        n_buses = r["ieee_data"]["num_buses"]
        ref_per_bus = dc_disp_consensus.get(sys_name)
        if ref_per_bus is None:
            continue
        for s_idx, sname in enumerate(solvers):
            d = r.get(sname)
            if not isinstance(d, dict) or "gen_dispatch_mw" not in d:
                continue
            marker = SOLVER_MARKERS.get(sname, "o")
            sol_per_bus = _gen_dispatch_per_bus(d, n_buses)
            idx = _sample_parity_idx(ref_per_bus, sol_per_bus)
            dx = (s_idx - (len(solvers) - 1) / 2) * offset_step
            ax1.scatter(
                ref_per_bus[idx] + dx, sol_per_bus[idx],
                c=sys_color, marker=marker,
                s=100, alpha=0.8, edgecolors="white", linewidths=0.4,
                zorder=3 + z_idx,
            )
            all_ref.extend(ref_per_bus)
            all_sol.extend(sol_per_bus)

    # AC overlay: hollow markers for dispatch parity
    if has_ac and ac_solvers:
        ac_disp_consensus = _compute_consensus_dispatch(acopf_results, ac_solvers)
        for r_ac in acopf_results:
            sys_name = r_ac["name"]
            ref_per_bus = ac_disp_consensus.get(sys_name)
            if ref_per_bus is None:
                continue
            sys_color = SYSTEM_COLORS.get(sys_name, "#999")
            n_buses = r_ac["ieee_data"]["num_buses"]
            for sname in ac_solvers:
                d = r_ac.get(sname)
                if not isinstance(d, dict) or "gen_dispatch_mw" not in d:
                    continue
                if "OPTIMAL" not in d.get("status", ""):
                    continue
                marker = SOLVER_MARKERS.get(sname, "o")
                sol_per_bus = _gen_dispatch_per_bus(d, n_buses)
                idx = _sample_parity_idx(ref_per_bus, sol_per_bus)
                ax1.scatter(
                    ref_per_bus[idx], sol_per_bus[idx],
                    facecolors="none", edgecolors=sys_color,
                    marker=marker, s=100, alpha=0.6, linewidths=1.2,
                    zorder=6,
                )
                all_ref.extend(ref_per_bus)
                all_sol.extend(sol_per_bus)

    _add_parity_line(ax1, all_ref, all_sol)
    ax1.set_aspect("auto")
    ax1.set_xlabel("Consensus Dispatch per Bus (MW)")
    ax1.set_ylabel("Solver Dispatch per Bus (MW)")
    ax1.grid(True, alpha=0.3, linestyle="--")
    ax_histx.set_title("(a) Generation Dispatch Parity")

    # ── Marginal histograms ──
    ref_arr = np.asarray(all_ref, dtype=float)
    sol_arr = np.asarray(all_sol, dtype=float)
    mask = np.isfinite(ref_arr) & np.isfinite(sol_arr)
    if mask.any():
        n_bins = 60
        ax_histx.hist(ref_arr[mask], bins=n_bins, color="#5a7d9a",
                       alpha=0.6, edgecolor="none", density=False, log=True)
        ax_histy.hist(sol_arr[mask], bins=n_bins, orientation="horizontal",
                       color="#5a7d9a", alpha=0.6, edgecolor="none", density=False, log=True)
    ax_histx.axis("off")
    ax_histy.axis("off")

    # ── Shared solver list and positions for (b) and (c) ──
    all_plot_solvers = list(solvers)
    for s in ac_solvers:
        if s not in all_plot_solvers:
            all_plot_solvers.append(s)

    positions = np.arange(len(all_plot_solvers))
    dc_x = -0.15 if has_ac else -0.20
    ac_x = -0.30

    # ── (b) |dispatch deviation| per generator on log scale ──
    ac_disp_consensus_b = _compute_consensus_dispatch(acopf_results, ac_solvers) if has_ac else {}
    violin_disp = [[] for _ in all_plot_solvers]
    for s_idx, sname in enumerate(all_plot_solvers):
        # DC deviations
        if sname in solvers:
            for r in results:
                d = r.get(sname)
                if not isinstance(d, dict) or "gen_dispatch_mw" not in d:
                    continue
                ref = dc_disp_consensus.get(r["name"])
                if ref is None:
                    continue
                n_buses = r["ieee_data"]["num_buses"]
                sol = _gen_dispatch_per_bus(d, n_buses)
                abs_err = np.abs(sol - ref)
                abs_err = np.where(abs_err < 1e-15, 1e-15, abs_err)
                violin_disp[s_idx].extend(abs_err.tolist())
                sampled = _sample_extremes(abs_err)
                sys_color = SYSTEM_COLORS.get(r["name"], "#999")
                jitter = (list(SYSTEM_COLORS.keys()).index(r["name"])
                          - len(SYSTEM_COLORS) / 2) * 0.02
                ax_disp.scatter(
                    np.full_like(sampled, s_idx + dc_x + jitter), sampled,
                    c=sys_color, marker="<",
                    s=80, alpha=0.6, edgecolors="none", zorder=3,
                )
        # AC deviations
        if has_ac and sname in ac_solvers:
            for r_ac in acopf_results:
                d = r_ac.get(sname)
                if not isinstance(d, dict) or "gen_dispatch_mw" not in d:
                    continue
                if "OPTIMAL" not in d.get("status", ""):
                    continue
                ref = ac_disp_consensus_b.get(r_ac["name"])
                if ref is None:
                    continue
                n_buses = r_ac["ieee_data"]["num_buses"]
                sol = _gen_dispatch_per_bus(d, n_buses)
                abs_err = np.abs(sol - ref)
                abs_err = np.where(abs_err < 1e-15, 1e-15, abs_err)
                violin_disp[s_idx].extend(abs_err.tolist())
                sampled = _sample_extremes(abs_err)
                sys_color = SYSTEM_COLORS.get(r_ac["name"], "#999")
                jitter = (list(SYSTEM_COLORS.keys()).index(r_ac["name"])
                          - len(SYSTEM_COLORS) / 2) * 0.02
                ax_disp.scatter(
                    np.full_like(sampled, s_idx + ac_x + jitter), sampled,
                    facecolors="none", edgecolors=sys_color, marker=">",
                    s=80, alpha=0.6, linewidths=0.8, zorder=4,
                )

    ax_disp.set_yscale("log")
    _add_log_violins(ax_disp, violin_disp, positions)
    ax_disp.set_xticks(positions)
    ax_disp.set_xticklabels([_display(s) for s in all_plot_solvers],
                            rotation=45, ha="right")
    ax_disp.set_ylabel("|Dispatch Deviation| (MW)")
    ax_disp.set_title("(b) Dispatch Deviation by Solver")
    for boundary in np.arange(0.5, len(all_plot_solvers) - 0.5, 1.0):
        ax_disp.axvline(boundary, color="0.65", linewidth=0.8, zorder=2)
    ax_disp.grid(axis="y", alpha=0.3, which="both")

    # ── (c) |cost deviation| on log scale ──
    dc_cost_consensus = _compute_consensus_cost(results)
    ac_cost_consensus = _compute_consensus_cost(acopf_results) if has_ac else {}
    violin_cost = [[] for _ in all_plot_solvers]
    for s_idx, sname in enumerate(all_plot_solvers):
        # DC deviations
        if sname in solvers:
            for r in results:
                d = r.get(sname)
                if not isinstance(d, dict) or "total_cost" not in d:
                    continue
                med_cost = dc_cost_consensus.get(r["name"])
                if med_cost is None or med_cost <= 0:
                    continue
                sol_cost = float(d["total_cost"])
                abs_dev = abs(sol_cost - med_cost) / med_cost * 100
                abs_dev = max(abs_dev, 1e-15)
                violin_cost[s_idx].append(abs_dev)
                sys_color = SYSTEM_COLORS.get(r["name"], "#999")
                jitter = (list(SYSTEM_COLORS.keys()).index(r["name"])
                          - len(SYSTEM_COLORS) / 2) * 0.03
                ax_cost.scatter(
                    s_idx + dc_x + jitter, abs_dev,
                    c=sys_color, marker="<",
                    s=80, alpha=0.8, edgecolors="white", linewidths=0.3,
                    zorder=3,
                )
        # AC deviations
        if has_ac and sname in ac_solvers:
            for r_ac in acopf_results:
                d = r_ac.get(sname)
                if not isinstance(d, dict) or "total_cost" not in d:
                    continue
                if "OPTIMAL" not in d.get("status", ""):
                    continue
                med_cost = ac_cost_consensus.get(r_ac["name"])
                if med_cost is None or med_cost <= 0:
                    continue
                sol_cost = float(d["total_cost"])
                abs_dev = abs(sol_cost - med_cost) / med_cost * 100
                abs_dev = max(abs_dev, 1e-15)
                violin_cost[s_idx].append(abs_dev)
                sys_color = SYSTEM_COLORS.get(r_ac["name"], "#999")
                jitter = (list(SYSTEM_COLORS.keys()).index(r_ac["name"])
                          - len(SYSTEM_COLORS) / 2) * 0.03
                ax_cost.scatter(
                    s_idx + ac_x + jitter, abs_dev,
                    facecolors="none", edgecolors=sys_color, marker=">",
                    s=80, alpha=0.6, linewidths=0.8, zorder=4,
                )

    ax_cost.set_yscale("log")
    _add_log_violins(ax_cost, violin_cost, positions)
    ax_cost.set_xticks(positions)
    ax_cost.set_xticklabels([_display(s) for s in all_plot_solvers],
                            rotation=45, ha="right")
    ax_cost.set_ylabel("|Cost Deviation| (%)")
    ax_cost.set_title("(c) Cost Agreement Magnitude")
    for boundary in np.arange(0.5, len(all_plot_solvers) - 0.5, 1.0):
        ax_cost.axvline(boundary, color="0.65", linewidth=0.8, zorder=2)
    ax_cost.grid(axis="y", alpha=0.3, which="both")

    _add_figure_legend(fig, all_plot_solvers, system_names, has_ac=has_ac)
    out_path = output_dir / "fig_generation_dispatch.pdf"
    out_path_1 = output_dir / "fig_generation_dispatch.png"
    out_path_2 = output_dir / "fig_generation_dispatch.svg"
    fig.savefig(out_path, dpi=300)
    fig.savefig(out_path_1, dpi=300)
    fig.savefig(out_path_2, dpi=300)
    print(f"  Saved {out_path}")
    plt.close(fig)


# ── Figure 4: Solve Times ────────────────────────────────────────────────


def _fit_complexity(
    bus_counts: list[int],
    times: list[float],
    exclude_jit_bus: int | None = None,
) -> tuple[float, float] | None:
    """Fit t = a * n^alpha via log-log linear regression.

    Parameters
    ----------
    bus_counts : list[int]
        Number of buses for each data point.
    times : list[float]
        Corresponding median solve times (seconds).
    exclude_jit_bus : int or None
        If set, exclude this bus count (JIT warmup outlier) from the fit.

    Returns
    -------
    (alpha, a) or None if fewer than 3 valid points.
    """
    xs, ys = [], []
    for n, t in zip(bus_counts, times):
        if n == exclude_jit_bus:
            continue
        if t > 0 and np.isfinite(t):
            xs.append(np.log10(n))
            ys.append(np.log10(t))
    if len(xs) < 3:
        return None
    alpha, log_a = np.polyfit(xs, ys, 1)
    return float(alpha), 10 ** float(log_a)


def plot_solve_times(results: list[dict], output_dir: Path,
                     acopf_results: list[dict] | None = None):
    """Figure 4: Solve time scaling (log-log) with stacked bars + heatmap.

    AC overlay: dashed lines on subplot (a) with same solver colors.
    """
    all_solvers = _get_solver_names(results)
    has_ac = acopf_results is not None and len(acopf_results) > 0

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 8))

    # ── (a) Scaling plot: errorbar lines (primary y) + stacked bars (secondary y) ──
    # Detect JIT warmup for annotation (but still plot all points)
    esfex_times_raw = {}
    for r in results:
        n = r["ieee_data"]["num_buses"]
        t = r.get("solve_times", {}).get("ESFEX", float("nan"))
        if not np.isnan(t):
            esfex_times_raw[n] = t
    jit_bus = None
    if len(esfex_times_raw) >= 2:
        sorted_buses = sorted(esfex_times_raw.keys())
        t_first = esfex_times_raw[sorted_buses[0]]
        t_second = esfex_times_raw[sorted_buses[1]]
        if t_first > 10 * t_second:
            jit_bus = sorted_buses[0]

    # Time lines with IQR error bars (primary y-axis, foreground)
    fit_labels = {}  # solver -> alpha string for legend
    for sname in all_solvers:
        n_buses = []
        times = []
        yerr_lo = []
        yerr_hi = []
        for r in results:
            n = r["ieee_data"]["num_buses"]
            t = r.get("solve_times", {}).get(sname, float("nan"))
            if not np.isnan(t) and t > 0:
                n_buses.append(n)
                times.append(t)
                iqr = r.get("solve_times_iqr", {}).get(sname)
                if iqr is not None:
                    yerr_lo.append(t - iqr[0])
                    yerr_hi.append(iqr[1] - t)
                else:
                    yerr_lo.append(0.0)
                    yerr_hi.append(0.0)
        if not n_buses:
            continue
        color = SOLVER_COLORS.get(sname, "#999")
        marker = SOLVER_MARKERS.get(sname, "o")

        # Plot with IQR error bars
        has_iqr = any(lo > 0 or hi > 0 for lo, hi in zip(yerr_lo, yerr_hi))
        if has_iqr:
            ax1.errorbar(
                n_buses, times,
                yerr=[yerr_lo, yerr_hi],
                marker=marker, color=color,
                linewidth=1.8, markersize=9, markeredgecolor="white",
                markeredgewidth=0.5, capsize=3, capthick=0.8,
                elinewidth=0.8, zorder=5,
            )
        else:
            ax1.plot(
                n_buses, times, marker=marker, color=color,
                linewidth=1.8, markersize=9, markeredgecolor="white",
                markeredgewidth=0.5, zorder=5,
            )        

        # Empirical complexity fit: t = a * n^alpha
        fit = _fit_complexity(n_buses, times, exclude_jit_bus=jit_bus)
        if fit is not None:
            alpha, a = fit
            fit_labels[sname] = f"\u03b1={alpha:.2f}"
            # Draw fitted dashed line across full bus range
            x_fit = np.logspace(
                np.log10(min(n_buses) * 0.8),
                np.log10(max(n_buses) * 1.2),
                100,
            )
            y_fit = a * x_fit ** alpha
            ax1.plot(x_fit, y_fit, "--", color=color, linewidth=0.9,
                     alpha=0.5, zorder=3)
            
    # Only show sensible tick labels (up to 10⁴ MB)
    from matplotlib.ticker import FixedLocator, FixedFormatter        

    ax1.set_xscale("log")
    ax1.set_yscale("log")
    ax1.set_ylim(bottom=1e-5)
    ax1.set_xlabel("Number of Buses")
    ax1.set_ylabel("Solve Time (s)")
    ax1.set_title("(a) Computational Scaling")
    ax1.grid(True, alpha=0.3, which="both")
    bus_counts = sorted(set(r["ieee_data"]["num_buses"] for r in results))
    ax1.set_xticks(bus_counts)
    ax1.set_xticklabels([str(b) for b in bus_counts], fontsize=9, rotation=45, ha="right")

    # AC solve times overlay (dashed lines, same colors)
    ac_fit_labels = {}
    if has_ac:
        ac_snames = set()
        for r_ac in acopf_results:
            for s in SOLVER_COLORS:
                if s in r_ac.get("solve_times", {}):
                    ac_snames.add(s)
        for sname in all_solvers:
            if sname not in ac_snames:
                continue
            ac_n, ac_t = [], []
            for r_ac in acopf_results:
                t = r_ac.get("solve_times", {}).get(sname, float("nan"))
                if not np.isnan(t) and t > 0:
                    ac_n.append(r_ac["ieee_data"]["num_buses"])
                    ac_t.append(t)
            if not ac_n:
                continue
            color = SOLVER_COLORS.get(sname, "#999")
            marker = SOLVER_MARKERS.get(sname, "o")
            ax1.plot(
                ac_n, ac_t, marker=marker, color=color,
                linewidth=1.5, markersize=7, markeredgecolor="white",
                markeredgewidth=0.5, linestyle="--", alpha=0.7, zorder=4,
            )
            # AC complexity fit
            ac_fit = _fit_complexity(ac_n, ac_t)
            if ac_fit is not None:
                alpha_ac, a_ac = ac_fit
                ac_fit_labels[sname] = f"\u03b1={alpha_ac:.2f}"
                x_fit = np.logspace(
                    np.log10(min(ac_n) * 0.8),
                    np.log10(max(ac_n) * 1.2),
                    100,
                )
                y_fit = a_ac * x_fit ** alpha_ac
                ax1.plot(x_fit, y_fit, ":", color=color, linewidth=0.9,
                         alpha=0.5, zorder=3)
        # Also add AC-only solvers (e.g. MATPOWER) not in all_solvers
        for sname in ac_snames - set(all_solvers):
            ac_n, ac_t = [], []
            for r_ac in acopf_results:
                t = r_ac.get("solve_times", {}).get(sname, float("nan"))
                if not np.isnan(t) and t > 0:
                    ac_n.append(r_ac["ieee_data"]["num_buses"])
                    ac_t.append(t)
            if not ac_n:
                continue
            color = SOLVER_COLORS.get(sname, "#999")
            marker = SOLVER_MARKERS.get(sname, "o")
            ax1.plot(
                ac_n, ac_t, marker=marker, color=color,
                linewidth=1.5, markersize=7, markeredgecolor="white",
                markeredgewidth=0.5, linestyle="--", alpha=0.7, zorder=4,
            )
            # AC complexity fit
            ac_fit = _fit_complexity(ac_n, ac_t)
            if ac_fit is not None:
                alpha_ac, a_ac = ac_fit
                ac_fit_labels[sname] = f"\u03b1={alpha_ac:.2f}"
                x_fit = np.logspace(
                    np.log10(min(ac_n) * 0.8),
                    np.log10(max(ac_n) * 1.2),
                    100,
                )
                y_fit = a_ac * x_fit ** alpha_ac
                ax1.plot(x_fit, y_fit, ":", color=color, linewidth=0.9,
                         alpha=0.5, zorder=3)

    # Peak memory bars (secondary y-axis, background)
    has_mem_data = any(r.get("peak_memory") for r in results)
    if has_mem_data:
        ax1_mem = ax1.twinx()
        ax1_mem.set_yscale("log")

        n_sol = len(all_solvers)
        log_buses = np.log10(np.array(bus_counts, dtype=float))
        if len(log_buses) > 1:
            min_gap = np.diff(log_buses).min()
            group_width = min_gap * 0.95
        else:
            group_width = 0.4
        bar_width = group_width / n_sol

        for s_idx, sname in enumerate(all_solvers):
            positions_log = log_buses + (s_idx - (n_sol - 1) / 2) * bar_width
            positions = 10 ** positions_log
            widths = [10 ** (pl + bar_width / 2) - 10 ** (pl - bar_width / 2)
                      for pl in positions_log]

            mem_vals = []
            for bc in bus_counts:
                m = 0.0
                for r in results:
                    if r["ieee_data"]["num_buses"] == bc:
                        m = r.get("peak_memory", {}).get(sname, 0.0)
                        break
                mem_vals.append(m if m > 0.1 else np.nan)

            color = SOLVER_COLORS.get(sname, "#999")
            ax1_mem.bar(positions, mem_vals, width=widths, color=color,
                        alpha=0.45, edgecolor=color, linewidth=0.7, zorder=2)

        # Expand upper limit so bars stay in lower portion of plot
        ax1_mem.set_ylim(0.1, 1e8)
        ax1_mem.set_ylabel("Peak Memory (MB)")
       
        ax1_mem.yaxis.set_major_locator(FixedLocator([1, 10, 100, 1000, 10000, 100000]))
        ax1_mem.yaxis.set_major_formatter(FixedFormatter(
            ["1", "10", "10²", "10³", "10⁴", "10⁵"]))
        ax1_mem.tick_params(axis="y")
        # Ensure time lines stay in front
        ax1.set_zorder(ax1_mem.get_zorder() + 1)
        ax1.patch.set_visible(False)

    # ── (b) Performance heatmap (DC + AC interleaved rows) ──
    short_names = [r["name"].replace("IEEE ", "").replace("PEGASE ", "P")
                   for r in results]
    n_sol = len(all_solvers)

    # Build AC lookup by system name
    ac_by_name = {}
    if has_ac:
        for r_ac in acopf_results:
            ac_by_name[r_ac["name"]] = r_ac

    # Build interleaved row structure: (system_idx, "DC"/"AC", label)
    rows = []
    for i, r in enumerate(results):
        rows.append((i, "DC", short_names[i]))
        if r["name"] in ac_by_name:
            rows.append((i, "AC", ""))  # AC row, no label (grouped)
    n_rows = len(rows)

    time_matrix = np.full((n_rows, n_sol), np.nan)
    solver_pct = np.full((n_rows, n_sol), np.nan)
    is_jit = np.full((n_rows, n_sol), False)
    is_fail = np.full((n_rows, n_sol), False)
    row_is_ac = [False] * n_rows

    def _check_fail(r_dict, sname):
        """Return True if solver was attempted but failed."""
        d = r_dict.get(sname)
        if isinstance(d, dict):
            st = d.get("status", "")
            return "OPTIMAL" not in st and st not in ("", "UNKNOWN")
        # No result dict but has timing → solver ran and failed
        return sname in r_dict.get("solve_times", {})

    for ri, (sys_i, kind, _lbl) in enumerate(rows):
        if kind == "DC":
            r = results[sys_i]
            times = r.get("solve_times", {})
            stimes = r.get("solver_times", {})
            n_buses = r["ieee_data"]["num_buses"]
            for j, sname in enumerate(all_solvers):
                t = times.get(sname, float("nan"))
                time_matrix[ri, j] = t
                st = stimes.get(sname, 0.0)
                if not np.isnan(t) and t > 0 and st > 0:
                    solver_pct[ri, j] = 100.0 * st / t
                if sname == "ESFEX" and n_buses == jit_bus:
                    is_jit[ri, j] = True
                if _check_fail(r, sname):
                    is_fail[ri, j] = True
        else:  # AC
            row_is_ac[ri] = True
            r_ac = ac_by_name[results[sys_i]["name"]]
            times = r_ac.get("solve_times", {})
            stimes = r_ac.get("solver_times", {})
            for j, sname in enumerate(all_solvers):
                t = times.get(sname, float("nan"))
                time_matrix[ri, j] = t
                st = stimes.get(sname, 0.0)
                if not np.isnan(t) and t > 0 and st > 0:
                    solver_pct[ri, j] = 100.0 * st / t
                if _check_fail(r_ac, sname):
                    is_fail[ri, j] = True

    # Log-normalized color scale (exclude JIT outlier and failures)
    time_matrix_display = time_matrix.copy()
    time_matrix_display[is_fail] = np.nan
    mask = ~np.isnan(time_matrix_display) & ~is_jit
    valid_times = time_matrix_display[mask]
    if len(valid_times) > 0:
        vmin = max(valid_times.min(), 1e-4)
        vmax = valid_times.max()
    else:
        vmin, vmax = 0.001, 10.0

    norm = mcolors.LogNorm(vmin=vmin, vmax=vmax)
    im = ax2.imshow(time_matrix_display, cmap="RdGy_r", norm=norm,
                    aspect="auto", interpolation="none")

    for ri in range(n_rows):
        for j in range(n_sol):
            t = time_matrix[ri, j]
            if is_fail[ri, j]:
                ax2.text(j, ri, "FAIL", ha="center", va="center",
                         fontsize=6, fontweight="bold", color="#f31d05")
                continue
            if np.isnan(t):
                ax2.text(j, ri, "N/A", ha="center", va="center",
                         fontsize=6, fontweight="bold", color="#0a0a0a")
                continue
            if t < 0.01:
                label = f"{t*1000:.1f}ms"
            elif t < 1:
                label = f"{t:.2f}s"
            else:
                label = f"{t:.1f}s"
            if is_jit[ri, j]:
                label += "*"
            pct = solver_pct[ri, j]
            if not np.isnan(pct):
                label += f"\n({pct:.0f}%S)"
            t_clamped = np.clip(t, vmin, vmax)
            brightness = 1.0 - (np.log10(t_clamped) - np.log10(vmin)) / \
                         (np.log10(vmax) - np.log10(vmin) + 1e-9)
            text_color = "white" if brightness < 0.25  or brightness > 0.75 else "black"
            ax2.text(j, ri, label, ha="center", va="center",
                     fontsize=6, fontweight="bold", color=text_color)

    # Y-axis labels: "system DC" / "system AC"
    row_labels = []
    for sys_i, kind, lbl in rows:
        row_labels.append(f"{short_names[sys_i]} {kind}")

    ax2.set_xticks(np.arange(n_sol))
    ax2.set_xticklabels([_display(s) for s in all_solvers], fontsize=9,
                        rotation=45, ha="center")
    ax2.set_yticks(np.arange(n_rows))
    ax2.set_yticklabels(row_labels, fontsize=8)

    # Draw separator lines between system groups
    for ri in range(n_rows):
        if row_is_ac[ri] and ri + 1 < n_rows and not row_is_ac[ri + 1]:
            ax2.axhline(ri + 0.5, color="0.3", linewidth=0.8)

    ax2.set_title("(b) Solve Time Heatmap")

    cbar = fig.colorbar(im, ax=ax2, shrink=0.8, pad=0.02)
    cbar.set_label("Solve Time (s)", fontsize=10)

    fig.tight_layout(rect=[0, 0.07, 1, 1], w_pad=2.0)
    # Unified solver legend below both subplots
    solver_handles = []
    for sname in all_solvers:
        color = SOLVER_COLORS.get(sname, "#999")
        marker = SOLVER_MARKERS.get(sname, "o")
        lbl = _display(sname)
        parts = []
        if sname in fit_labels:
            parts.append(f"DC {fit_labels[sname]}")
        if sname in ac_fit_labels:
            parts.append(f"AC {ac_fit_labels[sname]}")
        if parts:
            lbl += f" ({', '.join(parts)})"
        solver_handles.append(Line2D(
            [], [], marker=marker, color=color, linewidth=1.8,
            markeredgecolor="white", markeredgewidth=0.5,
            markersize=9, label=lbl,
        ))
    # DC/AC line style legend entries
    if has_ac:
        solver_handles.append(Line2D(
            [], [], color="0.4", linewidth=1.8, linestyle="-",
            label="DC",
        ))
        solver_handles.append(Line2D(
            [], [], color="0.4", linewidth=1.5, linestyle="--",
            alpha=0.7, label="AC",
        ))
    # Add proxy entry for memory bars
    if has_mem_data:
        solver_handles.append(Patch(
            facecolor="0.7", alpha=0.2, edgecolor="0.5",
            linewidth=0.5, label="Peak Memory (MB)",
        ))    
    fig.legend(
        handles=solver_handles, loc="lower center",
        bbox_to_anchor=(0.5, 0.0),
        ncol=6, fontsize=9,
        columnspacing=1.0, handletextpad=0.3,
        frameon=True, framealpha=0.9, edgecolor="0.8",
    )
    out_path = output_dir / "fig_solve_times.pdf"
    out_path_1 = output_dir / "fig_solve_times.png"
    out_path_2 = output_dir / "fig_solve_times.svg"
    fig.savefig(out_path, dpi=300)
    fig.savefig(out_path_1, dpi=300)
    fig.savefig(out_path_2, dpi=300)
    print(f"  Saved {out_path}")
    plt.close(fig)


def plot_solver_clustering(results: list[dict], output_dir: Path,
                          acopf_results: list[dict] | None = None):
    """Figure 6: Two-panel solver clustergram (DC and ACOPF).

    Panel (a) clusters solvers using 6 DC metrics per system.
    Panel (b) clusters solvers using 8 ACOPF metrics per system.
    Both use the original rectangular layout with full dendrograms.
    """
    from matplotlib.gridspec import GridSpec

    # Wrap all angles to [-180, 180] (physically meaningful range)
    results = _wrap_results_angles(results)
    if acopf_results:
        acopf_results = _wrap_results_angles(acopf_results)

    all_solvers = _get_solver_names(results)

    # ── DC consensus ──
    angle_consensus = _compute_consensus(results, "angles_deg", all_solvers)
    flow_consensus = _compute_consensus(results, "line_flows_mw", all_solvers)

    # ── AC consensus ──
    ac_angle_consensus: dict = {}
    ac_flow_consensus: dict = {}
    ac_vm_consensus: dict = {}
    ac_q_consensus: dict = {}
    if acopf_results:
        ac_solvers = _get_ac_solvers(acopf_results)
        ac_angle_consensus = _compute_consensus(acopf_results, "angles_deg", ac_solvers)
        ac_flow_consensus = _compute_consensus(acopf_results, "line_flows_mw", ac_solvers)
        ac_vm_consensus = _compute_consensus(acopf_results, "vm_pu", ac_solvers)
        ac_q_consensus = _compute_consensus(acopf_results, "line_flows_mvar", ac_solvers)
    ac_by_name = {r["name"]: r for r in acopf_results} if acopf_results else {}

    sys_short = [_short_name(r["name"]) for r in results]

    # ── Build DC feature matrix ──
    dc_metric_keys = ["time", "θ dev", "flow dev", "%S", "mem", "build"]
    dc_feat_names = [f"{sn} {mk}" for sn in sys_short for mk in dc_metric_keys]
    dc_rows = []
    for solver in all_solvers:
        feats = []
        for r in results:
            sys_name = r["name"]
            ref_ang = angle_consensus.get(sys_name)
            ref_fl = flow_consensus.get(sys_name)
            times = r.get("solve_times", {}).get(solver, [])
            s_times = r.get("solver_times", {}).get(solver, [])
            sol_d = r.get(solver, {})

            # 1) time
            feats.append(np.log10(max(float(np.median(times)), 1e-9)) if times else np.nan)
            # 2) angle dev
            sa = np.array(sol_d.get("angles_deg", []) if isinstance(sol_d, dict) else [], dtype=float)
            if ref_ang is not None and len(sa) == len(ref_ang):
                e = np.mean(np.abs(sa - ref_ang))
                feats.append(np.log10(e) if e > 0 else -15.0)
            else:
                feats.append(np.nan)
            # 3) flow dev
            sf = np.array(sol_d.get("line_flows_mw", []) if isinstance(sol_d, dict) else [], dtype=float)
            if ref_fl is not None and len(sf) == len(ref_fl):
                e = np.mean(np.abs(sf - ref_fl))
                feats.append(np.log10(e) if e > 0 else -15.0)
            else:
                feats.append(np.nan)
            # 4) %S
            if times and s_times:
                mt = float(np.median(times)); ms = float(np.median(s_times))
                feats.append(ms / mt if mt > 0 else np.nan)
            else:
                feats.append(np.nan)
            # 5) memory
            feats.append(np.log10(max(r.get("peak_memory", {}).get(solver, 0.0), 1e-3)))
            # 6) build
            bt = r.get("build_times", {}).get(solver, None)
            if bt is not None and bt > 0:
                feats.append(np.log10(bt))
            elif times:
                mt = float(np.median(times))
                ms = float(np.median(s_times)) if s_times else 0.0
                feats.append(np.log10(max(mt - ms, 1e-9)))
            else:
                feats.append(np.nan)
        dc_rows.append(feats)

    # ── Build AC feature matrix (exclude DC-only solvers) ──
    _DC_ONLY = {"scipy", "PyPSA"}
    ac_solvers_list = [s for s in all_solvers if s not in _DC_ONLY]
    ac_metric_keys = ["time", "θ dev", "P dev", "Vm dev", "Q dev", "%S", "mem", "build"]
    ac_feat_names = [f"{sn} {mk}" for sn in sys_short for mk in ac_metric_keys]
    ac_rows = []
    if acopf_results:
        for solver in ac_solvers_list:
            feats = []
            for r in results:
                sys_name = r["name"]
                ac_r = ac_by_name.get(sys_name)
                if ac_r is None:
                    feats.extend([np.nan] * 8)
                    continue
                sol_d = ac_r.get(solver, {})
                ac_ok = isinstance(sol_d, dict) and sol_d.get("status") == "OPTIMAL"
                ac_times = ac_r.get("solve_times", {}).get(solver, [])
                ac_s_times = ac_r.get("solver_times", {}).get(solver, [])

                # 1) time
                feats.append(np.log10(max(float(np.median(ac_times)), 1e-9))
                             if ac_ok and ac_times else np.nan)
                # 2) angle dev
                ref = ac_angle_consensus.get(sys_name)
                arr = np.array(sol_d.get("angles_deg", []), dtype=float) if ac_ok and sol_d.get("angles_deg") else np.array([])
                if ac_ok and ref is not None and len(arr) == len(ref):
                    e = np.mean(np.abs(arr - ref)); feats.append(np.log10(e) if e > 0 else -15.0)
                else:
                    feats.append(np.nan)
                # 3) flow dev
                ref = ac_flow_consensus.get(sys_name)
                arr = np.array(sol_d.get("line_flows_mw", []), dtype=float) if ac_ok and sol_d.get("line_flows_mw") else np.array([])
                if ac_ok and ref is not None and len(arr) == len(ref):
                    e = np.mean(np.abs(arr - ref)); feats.append(np.log10(e) if e > 0 else -15.0)
                else:
                    feats.append(np.nan)
                # 4) Vm dev
                ref = ac_vm_consensus.get(sys_name)
                arr = np.array(sol_d.get("vm_pu", []), dtype=float) if ac_ok and sol_d.get("vm_pu") else np.array([])
                if ac_ok and ref is not None and len(arr) == len(ref):
                    e = np.mean(np.abs(arr - ref)); feats.append(np.log10(e) if e > 0 else -15.0)
                else:
                    feats.append(np.nan)
                # 5) Q dev
                ref = ac_q_consensus.get(sys_name)
                arr = np.array(sol_d.get("line_flows_mvar", []), dtype=float) if ac_ok and sol_d.get("line_flows_mvar") else np.array([])
                if ac_ok and ref is not None and len(arr) == len(ref):
                    e = np.mean(np.abs(arr - ref)); feats.append(np.log10(e) if e > 0 else -15.0)
                else:
                    feats.append(np.nan)
                # 6) %S
                if ac_ok and ac_times and ac_s_times:
                    mt = float(np.median(ac_times)); ms = float(np.median(ac_s_times))
                    feats.append(ms / mt if mt > 0 else np.nan)
                else:
                    feats.append(np.nan)
                # 7) memory
                feats.append(np.log10(max(ac_r.get("peak_memory", {}).get(solver, 0.0), 1e-3))
                             if ac_ok else np.nan)
                # 8) build
                ac_bt = ac_r.get("build_times", {}).get(solver, None)
                if ac_ok and ac_bt is not None and ac_bt > 0:
                    feats.append(np.log10(ac_bt))
                elif ac_ok and ac_times:
                    mt = float(np.median(ac_times))
                    ms = float(np.median(ac_s_times)) if ac_s_times else 0.0
                    feats.append(np.log10(max(mt - ms, 1e-9)))
                else:
                    feats.append(np.nan)
            ac_rows.append(feats)

    # ── Helper to prepare a panel's data ──
    def _prepare(raw_rows, feat_names):
        X = np.array(raw_rows, dtype=float)
        valid = [j for j in range(X.shape[1])
                 if not np.all(np.isnan(X[:, j]))
                 and np.std(np.where(np.isnan(X[:, j]), np.nanmean(X[:, j]), X[:, j])) > 1e-12]
        X = X[:, valid]
        names = [feat_names[j] for j in valid]
        for j in range(X.shape[1]):
            cm = np.nanmean(X[:, j])
            X[:, j] = np.where(np.isnan(X[:, j]), cm, X[:, j])
        mu = X.mean(axis=0); sd = X.std(axis=0); sd[sd < 1e-12] = 1.0
        return (X - mu) / sd, names

    X_dc, dc_names = _prepare(dc_rows, dc_feat_names)

    # ── Helper to render one transposed clustergram panel ──
    # Transposed: features on y-axis (left dendro), solvers on x-axis (top dendro)
    def _render_panel(fig, gs_sub, X_std, feat_names, panel_label,
                      solver_list=None, show_cbar=True):
        """Render a single transposed clustergram panel."""
        solvers = solver_list if solver_list is not None else all_solvers
        # X_std: (n_solvers, n_features)
        Z_solver = linkage(X_std, method="ward", metric="euclidean")
        Z_feat = linkage(X_std.T, method="ward", metric="euclidean")

        # Grid: row0 = solver dendro (top), row1 = heatmap + feat dendro
        ax_top = fig.add_subplot(gs_sub[0, 1])    # solver dendrogram
        ax_left = fig.add_subplot(gs_sub[1, 0])   # feature dendrogram
        ax_heat = fig.add_subplot(gs_sub[1, 1])   # heatmap
        if show_cbar:
            ax_cbar = fig.add_subplot(gs_sub[1, 3])
        for pos in [(0, 0), (0, 2), (0, 3), (1, 2)]:
            ax_e = fig.add_subplot(gs_sub[pos[0], pos[1]])
            ax_e.axis("off")

        # Solver dendrogram (top — clusters the 8 solvers)
        ds = dendrogram(Z_solver, ax=ax_top, orientation="top",
                        no_labels=True, color_threshold=0,
                        above_threshold_color="0.4")
        ax_top.set_xticks([])
        for sp in ax_top.spines.values():
            sp.set_visible(False)
        ax_top.tick_params(left=False, labelleft=False)

        # Feature dendrogram (left — clusters the features)
        df = dendrogram(Z_feat, ax=ax_left, orientation="left",
                        no_labels=True, color_threshold=0,
                        above_threshold_color="0.4")
        ax_left.set_yticks([])
        for sp in ax_left.spines.values():
            sp.set_visible(False)
        ax_left.tick_params(bottom=False, labelbottom=False)

        # Reorder
        solver_order = ds["leaves"]
        feat_order = df["leaves"][::-1]

        # Transposed heatmap: (n_features, n_solvers)
        X_T = X_std.T
        X_plot = X_T[np.ix_(feat_order, solver_order)]

        vmax = np.abs(X_std).max()
        im = ax_heat.imshow(X_plot, cmap="RdYlBu_r", aspect="auto",
                            vmin=-vmax, vmax=vmax, interpolation="nearest")

        # X-axis: solvers (bottom)
        solver_names = [_display(solvers[i]) for i in solver_order]
        ax_heat.set_xticks(np.arange(len(solver_order)))
        ax_heat.set_xticklabels(solver_names, fontsize=24,
                                rotation=45, ha="right")

        # Y-axis: features (right)
        ordered_feats = [feat_names[j] for j in feat_order]
        ax_heat.set_yticks(np.arange(len(feat_order)))
        ax_heat.set_yticklabels(ordered_feats, fontsize=16)
        ax_heat.yaxis.tick_right()

        # Colorbar
        if show_cbar:
            cbar = fig.colorbar(im, cax=ax_cbar)
            cbar.set_label("Std. Value", fontsize=26)

        # Panel label
        ax_top.set_title(panel_label, fontsize=26, fontweight="bold",
                         loc="center", pad=4)

    # ── Figure with 1×2 layout (side by side) ──
    has_ac = acopf_results and len(ac_rows) > 0
    if has_ac:
        X_ac, ac_names = _prepare(ac_rows, ac_feat_names)

    n_dc_feats = X_dc.shape[1]
    n_ac_feats = X_ac.shape[1] if has_ac else 0
    dend_r = 0.08

    if has_ac:
        fig_h = max(10, 0.14 * max(n_dc_feats, n_ac_feats) + 2) + 4
        fig = plt.figure(figsize=(24, fig_h))
        outer = GridSpec(1, 2, wspace=0.12, figure=fig,
                         width_ratios=[n_dc_feats, n_ac_feats])
        gs_dc = outer[0].subgridspec(2, 4,
                    width_ratios=[dend_r, 1, 0.08, 0.02],
                    height_ratios=[dend_r, 1],
                    hspace=0.01, wspace=0.01)
        gs_ac = outer[1].subgridspec(2, 4,
                    width_ratios=[dend_r, 1, 0.25, 0.02],
                    height_ratios=[dend_r, 1],
                    hspace=0.01, wspace=0.01)
        _render_panel(fig, gs_dc, X_dc, dc_names, "(a) DCOPF",
                      show_cbar=False)
        _render_panel(fig, gs_ac, X_ac, ac_names, "(b) ACOPF",
                      solver_list=ac_solvers_list, show_cbar=True)
    else:
        fig = plt.figure(figsize=(10, 12))
        gs_dc = GridSpec(2, 4,
                    width_ratios=[dend_r, 1, 0.08, 0.02],
                    height_ratios=[dend_r, 1],
                    hspace=0.01, wspace=0.01,
                    figure=fig)
        _render_panel(fig, gs_dc, X_dc, dc_names, "DCOPF")

    fig.subplots_adjust(left=0.04, right=0.94, top=0.97, bottom=0.08)
    out_path = output_dir / "fig_solver_clustering.pdf"
    out_path_1 = output_dir / "fig_solver_clustering.png"
    fig.savefig(out_path, bbox_inches="tight", dpi=300)
    fig.savefig(out_path_1, bbox_inches="tight", dpi=300)
    print(f"  Saved {out_path}")
    plt.close(fig)


# ── Main entry point ─────────────────────────────────────────────────────


def generate_ieee_validation_plots(
    output_dir: str = "results/ieee_validation",
    sequential: bool = False,
    n_reps: int = 5,
):
    """Solve all IEEE systems and generate validation figures.

    Parameters
    ----------
    output_dir : str
        Directory for PDF output files.
    sequential : bool
        If True, solve systems one-by-one (useful for debugging).
        If False (default), solve in parallel using multiprocessing.
    n_reps : int
        Number of repetitions per (system, solver) pair for timing statistics.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    _setup_paper_style()

    print("Warming up Julia JIT...", end=" ", flush=True)
    t_jit = time.perf_counter()
    warmup_julia()
    print(f"done ({time.perf_counter() - t_jit:.1f}s)")

    print("Solving IEEE benchmark systems with ESFEX DCOPF...")
    systems = [ieee_9bus(), ieee_14bus(), ieee_30bus(), ieee_57bus()]

    # Add 118-bus and 300-bus if matpower is available
    try:
        from tests.fixtures.ieee_bus_data import ieee_118bus, ieee_300bus
        systems.append(ieee_118bus())
        systems.append(ieee_300bus())
    except ImportError:
        print("  (matpower not installed — skipping 118-bus and 300-bus)")

    # Report available external solvers
    try:
        from tests.fixtures.ieee_reference_solvers import get_available_solvers
        avail = get_available_solvers()
        if avail:
            print(f"  External solvers: {', '.join(avail.keys())}")
        else:
            print("  No external solvers available (install pypsa/pandapower/pypower)")
    except ImportError:
        print("  (ieee_reference_solvers not available)")

    t_total = time.perf_counter()

    if sequential:
        print(f"  Mode: sequential ({len(systems)} systems, {n_reps} reps)")
        results = [_solve_ieee_system(s, n_reps=n_reps) for s in systems]
    else:
        # Solve Julia solvers in main process (Julia is fork-unsafe)
        print(f"  Solving Julia solvers (main process, {len(systems)} systems, {n_reps} reps)...")
        esfex_outputs = _solve_julia_all(systems, n_reps=n_reps)

        # Parallelize Python-only solvers
        tasks = _build_task_list(systems, n_reps=n_reps)
        n_workers = min(len(tasks), mp.cpu_count() or 4)
        n_py_solvers = len(tasks) // len(systems) if systems else 0
        print(f"  Solving {len(tasks)} Python tasks in parallel "
              f"({len(systems)} systems x {n_py_solvers} solvers, "
              f"{n_workers} workers)")
        with mp.Pool(processes=n_workers, maxtasksperchild=1) as pool:
            task_outputs = pool.map(_solve_single_task, tasks)
        results = _assemble_results(esfex_outputs + task_outputs, systems)

    elapsed_total = time.perf_counter() - t_total
    print(f"\nAll tasks solved in {elapsed_total:.1f}s")

    print("Generating figures...")
    plot_voltage_angles(results, output_path)
    plot_line_flows(results, output_path)
    plot_generation_dispatch(results, output_path)
    plot_solve_times(results, output_path)
    plot_solver_clustering(results, output_path)

    print(f"\nAll figures saved to {output_path}/")


# =====================================================================
# ACOPF Benchmark — Solving Infrastructure
# =====================================================================


def _normalize_for_acopf(ieee_data: dict) -> dict:
    """Normalize ieee_data for fair ACOPF comparison across all solvers.

    Systems with fake thermal limits (uniform 9900 MW default from the
    MATPOWER parser, meaning the original data had rateA=0) get:
      - rate_mw = 0 (all solvers treat 0 as "no thermal limit")
      - Vm bounds relaxed to [0.80, 1.20] (helps NLP convergence)

    Returns the original dict unmodified when real ratings exist.
    """
    import copy

    lines = ieee_data["lines"]
    rates = set(l["rate_mw"] for l in lines)

    # Detect fake ratings: all identical and >= 9900 (parser default)
    has_fake_ratings = len(rates) == 1 and min(rates) >= 9900.0
    if not has_fake_ratings:
        return ieee_data

    data = copy.deepcopy(ieee_data)

    # Zero out fake thermal limits — all solvers treat 0 as unlimited
    for l in data["lines"]:
        l["rate_mw"] = 0.0

    # Relax voltage bounds for large systems without real ratings
    for b in data["buses"]:
        b["vmin_pu"] = 0.80
        b["vmax_pu"] = 1.20

    return data


def warmup_julia_acopf():
    """Trigger Ipopt JIT compilation with a trivial 2-bus AC system."""
    from esfex.bridge.julia_setup import get_esfex_module
    ESFEX = get_esfex_module()
    ESFEX.solve_acopf(
        num_buses=2,
        demand_p=np.array([0.0, 100.0]),
        demand_q=np.array([0.0, 10.0]),
        shunt_g=np.array([0.0, 0.0]),
        shunt_b=np.array([0.0, 0.0]),
        gen_bus=np.array([1], dtype=np.int64),
        gen_cost=np.array([10.0]),
        gen_pmax=np.array([200.0]),
        gen_pmin=np.array([0.0]),
        gen_qmax=np.array([200.0]),
        gen_qmin=np.array([-200.0]),
        line_from=np.array([1], dtype=np.int64),
        line_to=np.array([2], dtype=np.int64),
        line_r=np.array([0.01]),
        line_x=np.array([0.1]),
        line_b=np.array([0.0]),
        line_cap=np.array([200.0]),
        slack_bus=1,
        base_mva=100.0,
    )

    # Warm up PowerModels ACOPF if available
    try:
        from tests.fixtures.ieee_bus_data import ieee_9bus
        from tests.fixtures.ieee_reference_solvers import solve_acopf_powermodels
        solve_acopf_powermodels(ieee_9bus())
    except Exception:
        pass


def _call_esfex_acopf(ieee_data: dict, ESFEX) -> dict:
    """Call Julia solve_acopf and convert result to Python dict."""
    buses = ieee_data["buses"]
    gens = ieee_data["generators"]
    lines = ieee_data["lines"]
    n = ieee_data["num_buses"]

    jl_result = ESFEX.solve_acopf(
        num_buses=n,
        demand_p=np.array([b["pd_mw"] for b in buses], dtype=np.float64),
        demand_q=np.array([b.get("qd_mvar", 0.0) for b in buses], dtype=np.float64),
        shunt_g=np.array([b.get("gs_mw", 0.0) for b in buses], dtype=np.float64),
        shunt_b=np.array([b.get("bs_mvar", 0.0) for b in buses], dtype=np.float64),
        gen_bus=np.array([g["bus"] + 1 for g in gens], dtype=np.int64),
        gen_cost=np.array([g["cost_mwh"] for g in gens], dtype=np.float64),
        gen_pmax=np.array([g["pg_max"] for g in gens], dtype=np.float64),
        gen_pmin=np.array([g.get("pg_min", 0.0) for g in gens], dtype=np.float64),
        gen_qmax=np.array([min(g.get("qmax_mvar", 999.0), 9999.0) for g in gens], dtype=np.float64),
        gen_qmin=np.array([max(g.get("qmin_mvar", -999.0), -9999.0) for g in gens], dtype=np.float64),
        line_from=np.array([l["from"] + 1 for l in lines], dtype=np.int64),
        line_to=np.array([l["to"] + 1 for l in lines], dtype=np.int64),
        line_r=np.array([l.get("r_pu", 0.0) for l in lines], dtype=np.float64),
        line_x=np.array([l["x_pu"] for l in lines], dtype=np.float64),
        line_b=np.array([l.get("b_pu", 0.0) for l in lines], dtype=np.float64),
        line_cap=np.array([l["rate_mw"] for l in lines], dtype=np.float64),
        line_tap=np.array([l.get("tap", 1.0) for l in lines], dtype=np.float64),
        line_shift=np.array([l.get("shift_deg", 0.0) for l in lines], dtype=np.float64),
        vm_max=np.array([b.get("vmax_pu", 1.1) for b in buses], dtype=np.float64),
        vm_min=np.array([b.get("vmin_pu", 0.9) for b in buses], dtype=np.float64),
        vm_start=np.array([b.get("vm_pu", 1.0) for b in buses], dtype=np.float64),
        va_start=np.array([b.get("va_deg", 0.0) for b in buses], dtype=np.float64),
        pg_start=np.array([g.get("pg_mw", 0.0) for g in gens], dtype=np.float64),
        slack_bus=ieee_data["slack_bus"] + 1,
        base_mva=float(ieee_data["base_mva"]),
    )

    # Normalize Ipopt's LOCALLY_SOLVED to OPTIMAL
    raw_status = str(jl_result["status"])
    status = "OPTIMAL" if "LOCALLY_SOLVED" in raw_status or "OPTIMAL" in raw_status else raw_status

    return {
        "status": status,
        "total_cost": float(jl_result["total_cost"]),
        "angles_deg": list(jl_result["angles_deg"]),
        "vm_pu": list(jl_result["vm_pu"]),
        "line_flows_mw": list(jl_result["line_flows_mw"]),
        "line_flows_mvar": list(jl_result["line_flows_mvar"]),
        "gen_dispatch_list": list(jl_result["gen_dispatch_list"]),
        "gen_reactive_list": list(jl_result["gen_reactive_list"]),
        "gen_dispatch_mw": {int(k): float(v) for k, v in dict(jl_result["gen_dispatch_mw"]).items()},
        "_solver_time": float(jl_result.get("_solver_time", 0.0)),
    }


def _solve_julia_all_acopf(systems: list[dict], n_reps: int = 3) -> list[tuple]:
    """Solve all systems with Julia ACOPF solvers in the main process.

    Returns list of (name, solver, result, dt_list, mem_mb, solver_time_list).
    """
    from esfex.bridge.julia_setup import get_esfex_module
    ESFEX = get_esfex_module()

    # Check if PowerModels ACOPF is available
    pm_acopf = False
    try:
        from tests.fixtures.ieee_reference_solvers import solve_acopf_powermodels
        pm_acopf = True
    except ImportError:
        pass

    outputs = []
    for ieee_data in systems:
        ieee_data = _normalize_for_acopf(ieee_data)
        name = ieee_data["name"]

        # ── ESFEX ACOPF ──
        dt_list, st_list = [], []
        rfx_result = None
        _mem = _PeakMemoryTracker()
        for rep in range(n_reps):
            gc.collect()
            _mem.start()
            t0 = time.perf_counter()
            try:
                rep_result = _call_esfex_acopf(ieee_data, ESFEX)
                dt_list.append(time.perf_counter() - t0)
                st_list.append(rep_result.get("_solver_time", dt_list[-1]))
                if rfx_result is None:
                    rfx_result = rep_result
            except Exception as e:
                dt_list.append(time.perf_counter() - t0)
                st_list.append(dt_list[-1])
                if rfx_result is None:
                    rfx_result = {"status": f"FAILED: {e}", "total_cost": 0.0}
                    _safe_print(f"    {name} / ESFEX ACOPF: FAILED — {e}")
        mem_mb = _mem.stop()

        if rfx_result and "OPTIMAL" in rfx_result.get("status", ""):
            med = float(np.median(dt_list))
            _safe_print(f"    {name} / ESFEX ACOPF: ${rfx_result['total_cost']:,.0f} "
                        f"(median {med:.3f}s)")
            outputs.append((name, "ESFEX", rfx_result, dt_list, mem_mb, st_list))
        elif rfx_result:
            outputs.append((name, "ESFEX", rfx_result, dt_list, 0.0, st_list))

        # ── PowerModels ACOPF ──
        if pm_acopf:
            dt_list, st_list = [], []
            pm_result = None
            _mem = _PeakMemoryTracker()
            try:
                for rep in range(n_reps):
                    gc.collect()
                    _mem.start()
                    t0 = time.perf_counter()
                    rep_result = solve_acopf_powermodels(ieee_data)
                    dt_list.append(time.perf_counter() - t0)
                    st_list.append(rep_result.get("_solver_time", dt_list[-1]))
                    if pm_result is None:
                        pm_result = rep_result
                mem_mb = _mem.stop()
                med = float(np.median(dt_list))
                _safe_print(f"    {name} / PowerModels ACOPF: ${pm_result.get('total_cost', 0):,.0f} "
                            f"(median {med:.3f}s)")
                outputs.append((name, "PowerModels", pm_result, dt_list, mem_mb, st_list))
            except Exception as e:
                _safe_print(f"    {name} / PowerModels ACOPF: FAILED — {e}")

    return outputs


def _build_acopf_task_list(systems: list[dict], n_reps: int = 3) -> list[tuple]:
    """Build ACOPF tasks for parallel execution (Python-only solvers)."""
    _JULIA_SOLVERS = {"PowerModels"}

    solver_names = []
    try:
        from tests.fixtures.ieee_reference_solvers import get_available_acopf_solvers
        for name in get_available_acopf_solvers():
            if name not in _JULIA_SOLVERS:
                solver_names.append(name)
    except ImportError:
        pass

    tasks = []
    for ieee_data in systems:
        ieee_data = _normalize_for_acopf(ieee_data)
        for solver_name in solver_names:
            tasks.append((ieee_data, solver_name, n_reps, True))  # True = ACOPF
    return tasks


_ACOPF_TIMEOUT = 10000  # seconds per solver call (~167 min)


class _ACOPFTimeoutError(Exception):
    """Raised when an ACOPF solver exceeds the time limit."""


def _solve_acopf_single_task(args: tuple) -> tuple:
    """Solve a single ACOPF (system, solver) pair with N reps.

    Uses signal.alarm (Linux) to hard-kill solvers that exceed
    _ACOPF_TIMEOUT seconds (e.g. Egret/ipopt on large systems).
    """
    _ensure_sys_path()

    ieee_data, solver_name, n_reps = args[0], args[1], args[2]
    name = ieee_data["name"]

    _safe_print(f"    {name} / {solver_name} ACOPF: solving...")

    try:
        from tests.fixtures.ieee_reference_solvers import get_available_acopf_solvers
        solver_fn = get_available_acopf_solvers()[solver_name]

        dt_list, solver_time_list = [], []
        result = None
        _mem = _PeakMemoryTracker()
        for rep in range(n_reps):
            gc.collect()
            _mem.start()
            t0 = time.perf_counter()

            # Set up SIGALRM timeout (Linux only)
            import signal
            old_handler = signal.getsignal(signal.SIGALRM)

            def _timeout_handler(signum, frame):
                raise _ACOPFTimeoutError()

            try:
                signal.signal(signal.SIGALRM, _timeout_handler)
                signal.alarm(_ACOPF_TIMEOUT)
                # Retry once on transient numpy 2.x dimension errors
                # (caused by memory pressure under heavy parallelism)
                try:
                    rep_result = solver_fn(ieee_data)
                except ValueError as _ve:
                    if "same number of dimensions" in str(_ve):
                        gc.collect()
                        rep_result = solver_fn(ieee_data)
                    else:
                        raise
                signal.alarm(0)  # cancel alarm
            except _ACOPFTimeoutError:
                dt = time.perf_counter() - t0
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
                _safe_print(f"    {name} / {solver_name} ACOPF: TIMEOUT "
                            f"(killed at {dt:.0f}s > {_ACOPF_TIMEOUT}s)")
                return (name, solver_name, None, [dt], 0.0, [dt])
            finally:
                signal.signal(signal.SIGALRM, old_handler)

            dt = time.perf_counter() - t0
            dt_list.append(dt)
            solver_time_list.append(rep_result.get("_solver_time", dt))
            if result is None:
                result = rep_result
            else:
                del rep_result

        mem_mb = _mem.stop()
        median_t = float(np.median(dt_list))
        status = result.get("status", "?") if result else "FAILED"
        _safe_print(f"    {name} / {solver_name} ACOPF: median {median_t:.3f}s "
                    f"({status}, {mem_mb:.1f}MB)")
        return (name, solver_name, result, dt_list, mem_mb, solver_time_list)

    except Exception as e:
        _safe_print(f"    {name} / {solver_name} ACOPF: FAILED — {e}")
        return (name, solver_name, None, [0.0], 0.0, [0.0])


def _assemble_acopf_results(
    task_outputs: list[tuple],
    systems: list[dict],
) -> list[dict]:
    """Reassemble ACOPF task outputs into per-system result dicts."""
    by_name: dict[str, dict] = {}
    for ieee_data in systems:
        n = ieee_data["name"]
        by_name[n] = {
            "name": n,
            "ieee_data": ieee_data,
            "solve_times": {},
            "solve_times_all": {},
            "solve_times_iqr": {},
            "solver_times": {},
            "build_times": {},
            "peak_memory": {},
        }

    for sys_name, solver_name, result, dt_list, mem_mb, solver_time_list in task_outputs:
        entry = by_name.get(sys_name)
        if entry is None:
            continue

        median_t = float(np.median(dt_list))
        median_s = float(np.median(solver_time_list))
        entry["solve_times"][solver_name] = median_t
        entry["solve_times_all"][solver_name] = dt_list
        entry["solve_times_iqr"][solver_name] = (
            float(np.percentile(dt_list, 25)),
            float(np.percentile(dt_list, 75)),
        )
        entry["solver_times"][solver_name] = median_s
        entry["build_times"][solver_name] = max(0.0, median_t - median_s)
        entry["peak_memory"][solver_name] = mem_mb

        if result is not None:
            entry[solver_name] = result

    return [by_name[s["name"]] for s in systems]


# =====================================================================
# Figure 6: AC-Specific Quantities (Vm, Q, Losses)
# =====================================================================


def plot_ac_quantities(acopf_results: list[dict], output_dir: Path):
    """Figure 6: AC-specific physical quantities (2×2 layout).

    Row 1: (a) Vm parity, (c) Q parity
    Row 2: (b) Vm error strip, (d) Q error strip
    """
    if not acopf_results:
        return

    ac_solvers = _get_ac_solvers(acopf_results)
    if not ac_solvers:
        print("  Skipping AC quantities figure: no converged AC solvers.")
        return

    # Precompute consensus
    vm_consensus = _compute_consensus(acopf_results, "vm_pu", ac_solvers)
    q_consensus = _compute_consensus(acopf_results, "line_flows_mvar", ac_solvers)

    system_names = [r["name"] for r in acopf_results]
    all_plot_solvers = list(ac_solvers)

    from matplotlib.gridspec import GridSpec

    fig = plt.figure(figsize=(16, 12))
    outer = GridSpec(2, 2, figure=fig, hspace=0.35, wspace=0.35,
                     left=0.08, right=0.97, top=0.90, bottom=0.06)

    # (a) Vm parity with marginal histograms
    inner_vm = outer[0, 0].subgridspec(2, 2, width_ratios=[4, 1],
                                        height_ratios=[1, 4],
                                        hspace=0.05, wspace=0.10)
    ax_vm_par = fig.add_subplot(inner_vm[1, 0])
    ax_vm_histx = fig.add_subplot(inner_vm[0, 0], sharex=ax_vm_par)
    ax_vm_histy = fig.add_subplot(inner_vm[1, 1], sharey=ax_vm_par)
    fig.add_subplot(inner_vm[0, 1]).axis("off")

    # (b) Vm error strip
    ax_vm_err = fig.add_subplot(outer[1, 0])

    # (c) Q parity with marginal histograms
    inner_q = outer[0, 1].subgridspec(2, 2, width_ratios=[4, 1],
                                       height_ratios=[1, 4],
                                       hspace=0.05, wspace=0.10)
    ax_q_par = fig.add_subplot(inner_q[1, 0])
    ax_q_histx = fig.add_subplot(inner_q[0, 0], sharex=ax_q_par)
    ax_q_histy = fig.add_subplot(inner_q[1, 1], sharey=ax_q_par)
    fig.add_subplot(inner_q[0, 1]).axis("off")

    # (d) Q error strip
    ax_q_err = fig.add_subplot(outer[1, 1])

    sorted_results = sorted(acopf_results,
                            key=lambda r: r["ieee_data"]["num_buses"],
                            reverse=True)

    # ── (a) Voltage Magnitude Parity ────────────────────────────────
    all_ref_vm: list[float] = []
    all_sol_vm: list[float] = []

    for r in sorted_results:
        sys_name = r["name"]
        sys_color = SYSTEM_COLORS.get(sys_name, "#999")
        ref_vm = vm_consensus.get(sys_name)
        if ref_vm is None:
            continue
        for sname in ac_solvers:
            sol_d = r.get(sname)
            if not isinstance(sol_d, dict) or "vm_pu" not in sol_d:
                continue
            sol_vm = np.asarray(sol_d["vm_pu"], dtype=float)
            n = min(len(ref_vm), len(sol_vm))
            idx = _sample_parity_idx(ref_vm[:n], sol_vm[:n])
            marker = SOLVER_MARKERS.get(sname, "o")
            ax_vm_par.scatter(ref_vm[idx], sol_vm[idx], c=sys_color,
                              marker=marker, s=100, alpha=0.5,
                              edgecolors="none", zorder=3)
            all_ref_vm.extend(ref_vm[:n].tolist())
            all_sol_vm.extend(sol_vm[:n].tolist())

    _add_parity_line(ax_vm_par, all_ref_vm, all_sol_vm)
    ax_vm_par.set_aspect("auto")
    ax_vm_par.grid(True, alpha=0.3, linestyle="--")
    ax_vm_par.set_xlabel("Consensus |V| (p.u.)")
    ax_vm_par.set_ylabel("|V| solver (p.u.)")
    ax_vm_histx.set_title("(a) Vm Parity")

    # Vm marginal histograms
    _ref = np.asarray(all_ref_vm, dtype=float)
    _sol = np.asarray(all_sol_vm, dtype=float)
    _m = np.isfinite(_ref) & np.isfinite(_sol)
    if _m.any():
        ax_vm_histx.hist(_ref[_m], bins=60, color="#5a7d9a", alpha=0.6,
                          edgecolor="none", density=False, log=True)
        ax_vm_histy.hist(_sol[_m], bins=60, orientation="horizontal",
                          color="#5a7d9a", alpha=0.6, edgecolor="none", density=False, log=True)
    ax_vm_histx.axis("off")
    ax_vm_histy.axis("off")

    # ── (b) |Vm deviation| on log scale ────────────────────────────
    positions = np.arange(len(all_plot_solvers))
    violin_vm = [[] for _ in all_plot_solvers]
    for s_idx, sname in enumerate(all_plot_solvers):
        for r in acopf_results:
            d = r.get(sname)
            if not isinstance(d, dict) or "vm_pu" not in d:
                continue
            if "OPTIMAL" not in d.get("status", ""):
                continue
            ref = vm_consensus.get(r["name"])
            if ref is None:
                continue
            sol = np.asarray(d["vm_pu"], dtype=float)
            n = min(len(ref), len(sol))
            abs_err = np.abs(sol[:n] - ref[:n])
            abs_err = np.where(abs_err < 1e-15, 1e-15, abs_err)
            violin_vm[s_idx].extend(abs_err.tolist())
            sampled = _sample_extremes(abs_err)
            sys_color = SYSTEM_COLORS.get(r["name"], "#999")
            jitter = (list(SYSTEM_COLORS.keys()).index(r["name"])
                      - len(SYSTEM_COLORS) / 2) * 0.02
            ax_vm_err.scatter(
                np.full_like(sampled, s_idx - 0.20 + jitter), sampled,
                marker=">", s=60, alpha=0.6,facecolors="none", edgecolors=sys_color,
                zorder=3,
            )

    ax_vm_err.set_yscale("log")
    _add_log_violins(ax_vm_err, violin_vm, positions)
    ax_vm_err.set_xticks(positions)
    ax_vm_err.set_xticklabels([_display(s) for s in all_plot_solvers],
                              rotation=45, ha="right")
    ax_vm_err.set_ylabel("|Vm Deviation| (p.u.)")
    ax_vm_err.set_title("(b) Vm Deviation by Solver")
    for boundary in np.arange(0.5, len(all_plot_solvers) - 0.5, 1.0):
        ax_vm_err.axvline(boundary, color="0.65", linewidth=0.8, zorder=2)
    ax_vm_err.grid(axis="y", alpha=0.3, which="both")

    # ── (c) Reactive Power Flow Parity ──────────────────────────────
    all_ref_q: list[float] = []
    all_sol_q: list[float] = []

    for r in sorted_results:
        sys_name = r["name"]
        sys_color = SYSTEM_COLORS.get(sys_name, "#999")
        ref_q = q_consensus.get(sys_name)
        if ref_q is None:
            continue
        for sname in ac_solvers:
            sol_d = r.get(sname)
            if not isinstance(sol_d, dict) or "line_flows_mvar" not in sol_d:
                continue
            sol_q = np.asarray(sol_d["line_flows_mvar"], dtype=float)
            n = min(len(ref_q), len(sol_q))
            idx = _sample_parity_idx(ref_q[:n], sol_q[:n])
            marker = SOLVER_MARKERS.get(sname, ">")
            ax_q_par.scatter(ref_q[idx], sol_q[idx], c=sys_color,
                             marker=marker, s=100, alpha=0.5,
                             edgecolors="none", zorder=3)
            all_ref_q.extend(ref_q[:n].tolist())
            all_sol_q.extend(sol_q[:n].tolist())

    _add_parity_line(ax_q_par, all_ref_q, all_sol_q)
    ax_q_par.set_aspect("auto")
    ax_q_par.grid(True, alpha=0.3, linestyle="--")
    ax_q_par.set_xlabel("Consensus Q (MVAr)")
    ax_q_par.set_ylabel("Q solver (MVAr)")
    ax_q_histx.set_title("(c) Q Flow Parity")

    # Q marginal histograms
    _ref = np.asarray(all_ref_q, dtype=float)
    _sol = np.asarray(all_sol_q, dtype=float)
    _m = np.isfinite(_ref) & np.isfinite(_sol)
    if _m.any():
        ax_q_histx.hist(_ref[_m], bins=60, color="#5a7d9a", alpha=0.6,
                         edgecolor="none", density=False, log=True)
        ax_q_histy.hist(_sol[_m], bins=60, orientation="horizontal",
                         color="#5a7d9a", alpha=0.6, edgecolor="none", density=False, log=True)
    ax_q_histx.axis("off")
    ax_q_histy.axis("off")

    # ── (d) |Q deviation| on log scale ─────────────────────────────
    violin_q = [[] for _ in all_plot_solvers]
    for s_idx, sname in enumerate(all_plot_solvers):
        for r in acopf_results:
            d = r.get(sname)
            if not isinstance(d, dict) or "line_flows_mvar" not in d:
                continue
            if "OPTIMAL" not in d.get("status", ""):
                continue
            ref = q_consensus.get(r["name"])
            if ref is None:
                continue
            sol = np.asarray(d["line_flows_mvar"], dtype=float)
            n = min(len(ref), len(sol))
            abs_err = np.abs(sol[:n] - ref[:n])
            abs_err = np.where(abs_err < 1e-15, 1e-15, abs_err)
            violin_q[s_idx].extend(abs_err.tolist())
            sampled = _sample_extremes(abs_err)
            sys_color = SYSTEM_COLORS.get(r["name"], "#999")
            jitter = (list(SYSTEM_COLORS.keys()).index(r["name"])
                      - len(SYSTEM_COLORS) / 2) * 0.02
            ax_q_err.scatter(
                np.full_like(sampled, s_idx - 0.20 + jitter), sampled,
                marker=">", s=60, alpha=0.6, facecolors="none", edgecolors=sys_color,
                zorder=3,
            )

    ax_q_err.set_yscale("log")
    _add_log_violins(ax_q_err, violin_q, positions)
    ax_q_err.set_xticks(positions)
    ax_q_err.set_xticklabels([_display(s) for s in all_plot_solvers],
                             rotation=45, ha="right")
    ax_q_err.set_ylabel("|Q Deviation| (MVAr)")
    ax_q_err.set_title("(d) Q Deviation by Solver")
    for boundary in np.arange(0.5, len(all_plot_solvers) - 0.5, 1.0):
        ax_q_err.axvline(boundary, color="0.65", linewidth=0.8, zorder=2)
    ax_q_err.grid(axis="y", alpha=0.3, which="both")

    # ── Common legend ───────────────────────────────────────────────
    _add_figure_legend(fig, ac_solvers, system_names, ncol=8)
    out_path = output_dir / "fig_ac_quantities.pdf"
    out_path_1 = output_dir / "fig_ac_quantities.png"
    out_path_2 = output_dir / "fig_ac_quantities.svg"
    fig.savefig(out_path, dpi=300)
    fig.savefig(out_path_1, dpi=300)
    fig.savefig(out_path_2, dpi=300)
    print(f"  Saved {out_path_1}")
    plt.close(fig)


# =====================================================================
# ACOPF Benchmark — Standalone Plot Functions (legacy)
# =====================================================================


def plot_acopf_voltage_profiles(acopf_results: list[dict], output_path: Path):
    """Figure 6a: Voltage magnitude profiles across solvers for one system.

    Shows |V| per bus for each ACOPF solver overlaid on the same axes,
    with dashed lines for Vmin/Vmax bounds.
    """
    # Pick the largest system that has at least 2 converged solvers
    target = None
    for r in reversed(acopf_results):
        converged = [s for s in SOLVER_COLORS if s in r and
                     "OPTIMAL" in r[s].get("status", "")]
        if len(converged) >= 2 and r.get("vm_pu_from", None) is None:
            # Check that at least one solver has vm_pu
            has_vm = any("vm_pu" in r[s] for s in converged)
            if has_vm:
                target = r
                break
    if target is None and acopf_results:
        target = acopf_results[0]
    if target is None:
        return

    n = target["ieee_data"]["num_buses"]
    buses = target["ieee_data"]["buses"]
    name = target["name"]

    fig, ax = plt.subplots(1, 1, figsize=(12, 5))
    bus_indices = np.arange(n)

    solver_list = [s for s in SOLVER_COLORS if s in target and
                   "vm_pu" in target.get(s, {})]

    for sname in solver_list:
        vm = target[sname]["vm_pu"]
        color = SOLVER_COLORS.get(sname, "#999")
        ax.plot(bus_indices, vm, color=color, linewidth=1.2,
                alpha=0.8, label=_display(sname))

    # Voltage bounds
    vmin = [b.get("vmin_pu", 0.9) for b in buses]
    vmax = [b.get("vmax_pu", 1.1) for b in buses]
    ax.plot(bus_indices, vmin, "k--", linewidth=0.8, alpha=0.5, label="$V_{min}$/$V_{max}$")
    ax.plot(bus_indices, vmax, "k--", linewidth=0.8, alpha=0.5)

    ax.set_xlabel("Bus index")
    ax.set_ylabel("Voltage magnitude (p.u.)")
    ax.set_title(f"(a) ACOPF voltage profiles — {name}")
    ax.legend(fontsize=11, ncol=3)
    ax.set_xlim(-1, n)

    fig.tight_layout()
    fig.savefig(output_path / "fig6a_acopf_voltage_profiles.pdf", dpi=300)
    plt.close(fig)
    print(f"  fig6a_acopf_voltage_profiles.pdf ({name})")


def plot_acopf_agreement(acopf_results: list[dict], output_path: Path):
    """Figure 6b: Pairwise solver agreement heatmap (RMSE of Vm across systems).

    Lower RMSE = better agreement between two solvers.
    """
    # Collect all converged solver names
    all_solvers = []
    for r in acopf_results:
        for s in SOLVER_COLORS:
            if s in r and "vm_pu" in r.get(s, {}) and s not in all_solvers:
                if "OPTIMAL" in r[s].get("status", ""):
                    all_solvers.append(s)

    if len(all_solvers) < 2:
        print("  fig6b skipped: fewer than 2 converged ACOPF solvers")
        return

    n_solvers = len(all_solvers)
    rmse_matrix = np.zeros((n_solvers, n_solvers))

    for i, s1 in enumerate(all_solvers):
        for j, s2 in enumerate(all_solvers):
            if i == j:
                continue
            diffs_sq = []
            for r in acopf_results:
                if s1 in r and s2 in r and "vm_pu" in r.get(s1, {}) and "vm_pu" in r.get(s2, {}):
                    v1 = np.array(r[s1]["vm_pu"])
                    v2 = np.array(r[s2]["vm_pu"])
                    if len(v1) == len(v2):
                        diffs_sq.extend(((v1 - v2) ** 2).tolist())
            if diffs_sq:
                rmse_matrix[i, j] = np.sqrt(np.mean(diffs_sq))

    fig, ax = plt.subplots(1, 1, figsize=(7, 6))
    labels = [_display(s) for s in all_solvers]

    im = ax.imshow(rmse_matrix, cmap="YlOrRd", aspect="equal")
    ax.set_xticks(range(n_solvers))
    ax.set_yticks(range(n_solvers))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=12)
    ax.set_yticklabels(labels, fontsize=12)
    ax.set_title("(b) Cross-solver Vm RMSE")

    # Annotate cells
    for i in range(n_solvers):
        for j in range(n_solvers):
            if i != j:
                val = rmse_matrix[i, j]
                color = "white" if val > rmse_matrix.max() * 0.6 else "black"
                ax.text(j, i, f"{val:.1e}", ha="center", va="center",
                        fontsize=9, color=color)

    fig.colorbar(im, ax=ax, label="RMSE (p.u.)", shrink=0.8)
    fig.tight_layout()
    fig.savefig(output_path / "fig6b_acopf_agreement.pdf", dpi=300)
    plt.close(fig)
    print(f"  fig6b_acopf_agreement.pdf ({n_solvers} solvers)")


def plot_acopf_solve_times(acopf_results: list[dict], output_path: Path):
    """Figure 7a: ACOPF solve times (log-log), mirroring DCOPF Fig 4a."""
    fig, ax = plt.subplots(1, 1, figsize=(10, 6))

    solver_names = []
    for s in SOLVER_COLORS:
        if any(s in r.get("solve_times", {}) for r in acopf_results):
            solver_names.append(s)

    if not solver_names:
        print("  fig7a skipped: no ACOPF solve time data")
        plt.close(fig)
        return

    for sname in solver_names:
        x_vals, y_vals = [], []
        for r in acopf_results:
            if sname in r.get("solve_times", {}):
                n_bus = r["ieee_data"]["num_buses"]
                t = r["solve_times"][sname]
                x_vals.append(n_bus)
                y_vals.append(t)

        color = SOLVER_COLORS.get(sname, "#999")
        marker = SOLVER_MARKERS.get(sname, "o")
        ax.plot(x_vals, y_vals, marker=marker, color=color, linewidth=1.5,
                markersize=8, label=_display(sname), alpha=0.85)

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Number of buses")
    ax.set_ylabel("Median solve time (s)")
    ax.set_title("(a) ACOPF solve times")
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3, which="both")

    fig.tight_layout()
    fig.savefig(output_path / "fig7a_acopf_solve_times.pdf", dpi=300)
    plt.close(fig)
    print(f"  fig7a_acopf_solve_times.pdf ({len(solver_names)} solvers)")


def plot_acopf_vs_dcopf_cost(
    acopf_results: list[dict],
    dcopf_results: list[dict],
    output_path: Path,
):
    """Figure 7b: DC approximation gap — (AC_cost - DC_cost)/DC_cost per system.

    Shows how much more expensive the AC solution is compared to the DC
    approximation, quantifying the quality of the DC approximation.
    """
    # Match systems between ACOPF and DCOPF results
    dc_costs = {}
    for r in dcopf_results:
        # Use ESFEX DCOPF cost as the DC reference
        rfx = r.get("ESFEX", {})
        if rfx:
            dc_costs[r["name"]] = rfx.get("total_cost", 0.0)

    system_names = []
    gaps = []
    for r in acopf_results:
        name = r["name"]
        dc_c = dc_costs.get(name, 0.0)
        if dc_c <= 0:
            continue
        # Use the median ACOPF cost across converged solvers
        ac_costs = []
        for s in SOLVER_COLORS:
            if s in r and "OPTIMAL" in r[s].get("status", ""):
                ac_costs.append(r[s]["total_cost"])
        if not ac_costs:
            continue
        ac_c = float(np.median(ac_costs))
        gap_pct = (ac_c - dc_c) / dc_c * 100.0
        system_names.append(_short_name(name))
        gaps.append(gap_pct)

    if not system_names:
        print("  fig7b skipped: no matched AC/DC systems")
        return

    fig, ax = plt.subplots(1, 1, figsize=(10, 5))
    x = np.arange(len(system_names))
    colors = ["#2980b9" if g >= 0 else "#c0392b" for g in gaps]
    ax.bar(x, gaps, color=colors, width=0.6, edgecolor="white", linewidth=0.5)
    ax.set_xticks(x)
    ax.set_xticklabels(system_names, fontsize=12)
    ax.set_ylabel("Cost gap (%)")
    ax.set_title("(b) ACOPF vs DCOPF cost gap")
    ax.axhline(0, color="black", linewidth=0.5)
    ax.grid(True, axis="y", alpha=0.3)

    # Annotate bars
    for i, g in enumerate(gaps):
        va = "bottom" if g >= 0 else "top"
        ax.text(i, g, f"{g:+.1f}%", ha="center", va=va, fontsize=10, fontweight="bold")

    fig.tight_layout()
    fig.savefig(output_path / "fig7b_acopf_vs_dcopf_cost.pdf", dpi=300)
    plt.close(fig)
    print(f"  fig7b_acopf_vs_dcopf_cost.pdf ({len(system_names)} systems)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate IEEE DCOPF validation plots for ESFEX."
    )
    parser.add_argument(
        "--output-dir", default="results/ieee_validation",
        help="Output directory for PDF figures (default: results/ieee_validation)",
    )
    parser.add_argument(
        "--sequential", action="store_true",
        help="Solve systems sequentially instead of in parallel",
    )
    parser.add_argument(
        "--repeats", type=int, default=5,
        help="Number of repetitions per (system, solver) pair (default: 5)",
    )
    args = parser.parse_args()
    generate_ieee_validation_plots(args.output_dir, args.sequential, args.repeats)
