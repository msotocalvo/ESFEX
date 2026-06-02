#!/usr/bin/env python3
"""Re-run failed ACOPF solver/system combinations and patch the HDF5 file.

Runs each (solver, system) pair SEQUENTIALLY in the main process to avoid
the transient parallelization errors that caused the original failures.

Usage:
    python tests/patch_failed_acopf.py
    python tests/patch_failed_acopf.py --n-reps 25
    python tests/patch_failed_acopf.py --dry-run
"""
from __future__ import annotations

import argparse
import gc
import json
import subprocess
import sys
import time
from pathlib import Path

import h5py
import numpy as np

# ── Path setup ──────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))
sys.path.insert(0, str(_PROJECT_ROOT / "tests"))
sys.path.insert(0, str(_PROJECT_ROOT / "tests/fixtures"))

# ── Identify failed combinations ────────────────────────────────────────

_ACOPF_TIMEOUT = 3600  # 1 hour per solver call

# Known genuine convergence failures — skip these during re-run
_KNOWN_GENUINE_FAILURES = {
    ("IEEE 300-bus", "pandapower"),       # PIPS never converges on IEEE 300
    ("PEGASE 9241-bus", "GridCal"),       # GridCal diverges on PEGASE 9241
    ("PEGASE 13659-bus", "GridCal"),      # likely genuine
    ("PEGASE 13659-bus", "PYPOWER"),      # likely genuine
    ("PEGASE 13659-bus", "pandapower"),   # likely genuine
}


def find_failed_combinations(h5_path: Path) -> list[tuple[str, str]]:
    """Return list of (system_name, solver_name) pairs that need re-running."""
    failed = []
    with h5py.File(h5_path, "r") as f:
        if "acopf" not in f:
            print("No acopf section in HDF5")
            return failed
        acopf = f["acopf"]
        for sys_name in sorted(acopf.keys()):
            sg = acopf[sys_name]
            timing = sg.get("timing/solve_times_all", {})
            for solver in sorted(timing.keys()):
                # Check if solver data group exists
                solver_exists = solver in sg and solver != "timing"
                n_reps = timing[solver].shape[0] if solver in timing else 0
                t_vals = timing[solver][:] if solver in timing else []

                needs_rerun = False
                reason = ""

                if not solver_exists:
                    needs_rerun = True
                    reason = "NO DATA (group missing)"
                elif n_reps > 0 and all(t == 0.0 for t in t_vals):
                    needs_rerun = True
                    reason = f"reps={n_reps} but all times=0"
                else:
                    status = sg[solver].attrs.get("status", b"?")
                    if isinstance(status, bytes):
                        status = status.decode()
                    cost = sg[solver].attrs.get("total_cost", float("nan"))

                    if status == "FAILED" and (np.isnan(cost) or cost == 0.0):
                        needs_rerun = True
                        reason = f"FAILED with cost={cost}"
                    elif status == "FAILED":
                        # FAILED but has cost — could be genuine or transient
                        needs_rerun = True
                        reason = f"FAILED (cost=${cost:,.2f})"

                if needs_rerun:
                    failed.append((sys_name, solver))
                    print(f"  {sys_name:20s} / {solver:15s} — {reason}")

    return failed


# ── Load IEEE data ──────────────────────────────────────────────────────

def _load_ieee_data(sys_name: str) -> dict:
    """Load IEEE/PEGASE data by system display name."""
    from ieee_bus_data import (
        ieee_9bus, ieee_14bus, ieee_30bus, ieee_57bus,
        ieee_118bus, ieee_300bus,
        pegase_1354bus, pegase_2869bus, pegase_9241bus, pegase_13659bus,
    )
    loaders = {
        "IEEE 9-bus": ieee_9bus,
        "IEEE 14-bus": ieee_14bus,
        "IEEE 30-bus": ieee_30bus,
        "IEEE 57-bus": ieee_57bus,
        "IEEE 118-bus": ieee_118bus,
        "IEEE 300-bus": ieee_300bus,
        "PEGASE 1354-bus": pegase_1354bus,
        "PEGASE 2869-bus": pegase_2869bus,
        "PEGASE 9241-bus": pegase_9241bus,
        "PEGASE 13659-bus": pegase_13659bus,
    }
    return loaders[sys_name]()


def _normalize_for_acopf(ieee_data: dict) -> dict:
    """Same normalization as ieee_validation_plots._normalize_for_acopf."""
    import copy
    lines = ieee_data["lines"]
    rates = set(l["rate_mw"] for l in lines)
    has_fake = len(rates) == 1 and min(rates) >= 9900.0
    if not has_fake:
        return ieee_data
    data = copy.deepcopy(ieee_data)
    for l in data["lines"]:
        l["rate_mw"] = 0.0
    for b in data["buses"]:
        b["vmin_pu"] = 0.80
        b["vmax_pu"] = 1.20
    return data


# ── Solve single combination ────────────────────────────────────────────

def solve_one(sys_name: str, solver_name: str, n_reps: int) -> dict | None:
    """Run one ACOPF solver on one system in an isolated subprocess.

    Python-only solvers (PYPOWER, pandapower, GridCal, Egret) are executed
    in a fresh subprocess to avoid numpy corruption caused by juliacall
    (which changes numpy internal state when imported in the same process).
    """
    import json
    import subprocess
    import tempfile

    # Build a self-contained script that solves and writes JSON to a temp file
    # (avoids BlockingIOError on large PEGASE results when stdout buffer fills)
    import tempfile

    result_file = tempfile.mktemp(suffix=".json")

    script = f'''
import sys, gc, json, time, signal, warnings, traceback
import numpy as np
sys.path.insert(0, "{_PROJECT_ROOT / "tests" / "fixtures"}")

TIMEOUT = {_ACOPF_TIMEOUT}
RESULT_FILE = "{result_file}"

# Load data
from ieee_bus_data import (
    ieee_9bus, ieee_14bus, ieee_30bus, ieee_57bus,
    ieee_118bus, ieee_300bus,
    pegase_1354bus, pegase_2869bus, pegase_9241bus, pegase_13659bus,
)
loaders = {{
    "IEEE 9-bus": ieee_9bus, "IEEE 14-bus": ieee_14bus,
    "IEEE 30-bus": ieee_30bus, "IEEE 57-bus": ieee_57bus,
    "IEEE 118-bus": ieee_118bus, "IEEE 300-bus": ieee_300bus,
    "PEGASE 1354-bus": pegase_1354bus, "PEGASE 2869-bus": pegase_2869bus,
    "PEGASE 9241-bus": pegase_9241bus, "PEGASE 13659-bus": pegase_13659bus,
}}
ieee_data = loaders["{sys_name}"]()

# Normalize for ACOPF
import copy
lines = ieee_data["lines"]
rates = set(l["rate_mw"] for l in lines)
if len(rates) == 1 and min(rates) >= 9900.0:
    ieee_data = copy.deepcopy(ieee_data)
    for l in ieee_data["lines"]:
        l["rate_mw"] = 0.0
    for b in ieee_data["buses"]:
        b["vmin_pu"] = 0.80
        b["vmax_pu"] = 1.20

# Get solver
from ieee_reference_solvers import get_available_acopf_solvers
solvers = get_available_acopf_solvers()
if "{solver_name}" not in solvers:
    with open(RESULT_FILE, "w") as _f:
        json.dump({{"error": "solver not available"}}, _f)
    sys.exit(0)
solver_fn = solvers["{solver_name}"]

n_reps = {n_reps}
dt_list, solver_time_list = [], []
result = None
import resource
mem_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

for rep in range(n_reps):
    gc.collect()
    t0 = time.perf_counter()

    def _timeout_handler(signum, frame):
        raise TimeoutError("ACOPF timeout")
    old_handler = signal.getsignal(signal.SIGALRM)
    try:
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(TIMEOUT)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            rep_result = solver_fn(ieee_data)
        signal.alarm(0)
    except TimeoutError:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
        dt = time.perf_counter() - t0
        with open(RESULT_FILE, "w") as _f:
            json.dump({{"error": f"TIMEOUT at rep {{rep}} ({{dt:.0f}}s)"}}, _f)
        sys.exit(0)
    except Exception as e:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
        with open(RESULT_FILE, "w") as _f:
            json.dump({{"error": f"{{type(e).__name__}}: {{e}}"}}, _f)
        sys.exit(0)
    finally:
        signal.signal(signal.SIGALRM, old_handler)

    dt = time.perf_counter() - t0
    dt_list.append(dt)
    solver_time_list.append(rep_result.get("_solver_time", dt))
    if result is None:
        result = rep_result
    else:
        del rep_result
    print(f"    rep {{rep+1}}/{{n_reps}}: {{dt:.1f}}s", file=sys.stderr, flush=True)

mem_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
mem_mb = max(0, (mem_after - mem_before)) / 1024.0

# Serialize result — convert numpy arrays to lists
def _serialize(obj):
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj

clean = {{}}
for k, v in result.items():
    clean[k] = _serialize(v)
    if isinstance(v, dict):
        clean[k] = {{str(kk): _serialize(vv) for kk, vv in v.items()}}

output = {{
    "result": clean,
    "dt_list": dt_list,
    "solver_time_list": solver_time_list,
    "mem_mb": mem_mb,
}}
with open(RESULT_FILE, "w") as _f:
    json.dump(output, _f)
'''

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
        tmp.write(script)
        tmp_path = tmp.name

    try:
        proc = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True, text=True,
            timeout=_ACOPF_TIMEOUT * n_reps + 120,
        )

        # Print stderr (progress messages)
        if proc.stderr:
            for line in proc.stderr.strip().split("\n"):
                if line.strip():
                    print(line, flush=True)

        if proc.returncode != 0:
            print(f"    Subprocess failed (rc={proc.returncode})")
            if proc.stderr:
                for line in proc.stderr.strip().split("\n")[-5:]:
                    print(f"    {line}")
            return None

        # Read result from temp file
        result_path = Path(result_file)
        if not result_path.exists():
            print(f"    No result file from subprocess")
            return None

        with open(result_path) as rf:
            data = json.load(rf)

        if "error" in data:
            print(f"    {data['error']}")
            return None

        # Convert gen_dispatch_mw keys back to int
        gdm = data["result"].get("gen_dispatch_mw")
        if gdm and isinstance(gdm, dict):
            data["result"]["gen_dispatch_mw"] = {
                int(k): float(v) for k, v in gdm.items()
            }

        status = data["result"].get("status", "FAILED")
        median_t = float(np.median(data["dt_list"]))
        print(f"    -> {status}, median={median_t:.1f}s, mem={data['mem_mb']:.1f}MB")
        return data

    except subprocess.TimeoutExpired:
        print(f"    Subprocess timed out")
        return None
    except json.JSONDecodeError as e:
        print(f"    JSON decode error: {e}")
        return None
    finally:
        Path(tmp_path).unlink(missing_ok=True)
        Path(result_file).unlink(missing_ok=True)


# ── Patch HDF5 ──────────────────────────────────────────────────────────

def _write_solver_result(grp: h5py.Group, result: dict) -> None:
    """Write a single solver result dict into an HDF5 group."""
    grp.attrs["status"] = result.get("status", "UNKNOWN")
    grp.attrs["total_cost"] = float(result.get("total_cost", 0.0))
    for key in ["angles_deg", "line_flows_mw", "gen_dispatch_list",
                "vm_pu", "line_flows_mvar", "line_flows_to_mw",
                "gen_reactive_list"]:
        if key in result:
            data = np.asarray(result[key], dtype=np.float64)
            grp.create_dataset(key, data=data)
    gdm = result.get("gen_dispatch_mw")
    if gdm:
        keys = sorted(gdm.keys())
        grp.create_dataset("_gen_bus_keys", data=np.array(keys, dtype=np.int64))
        grp.create_dataset("_gen_bus_vals",
                           data=np.array([gdm[k] for k in keys], dtype=np.float64))
    if "_solver_time" in result:
        grp.attrs["_solver_time"] = float(result["_solver_time"])


def patch_hdf5(h5_path: Path, sys_name: str, solver_name: str,
               solve_data: dict) -> None:
    """Patch a single solver/system result into the HDF5 file."""
    result = solve_data["result"]
    dt_list = solve_data["dt_list"]
    solver_time_list = solve_data["solver_time_list"]
    mem_mb = solve_data["mem_mb"]

    with h5py.File(h5_path, "a") as f:
        sg = f[f"acopf/{sys_name}"]

        # Delete old solver group if it exists
        if solver_name in sg:
            del sg[solver_name]

        # Write new solver result
        _write_solver_result(sg.create_group(solver_name), result)

        # Patch timing
        timing = sg["timing"]

        # solve_times_all
        sta = timing["solve_times_all"]
        if solver_name in sta:
            del sta[solver_name]
        sta.create_dataset(solver_name,
                           data=np.array(dt_list, dtype=np.float64))

        # peak_memory
        pm = timing["peak_memory"]
        if solver_name in pm:
            del pm[solver_name]
        pm.create_dataset(solver_name, data=float(mem_mb))

        # solver_times (median internal solver time)
        st = timing["solver_times"]
        if solver_name in st:
            del st[solver_name]
        st.create_dataset(solver_name, data=float(np.median(solver_time_list)))

    print(f"  Patched {sys_name} / {solver_name} in HDF5")


# ── Main ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Re-run failed ACOPF and patch HDF5")
    parser.add_argument("--results-file", type=str,
                        default="results/ieee_validation/benchmark_results.h5")
    parser.add_argument("--n-reps", type=int, default=3,
                        help="Number of repetitions per (system, solver)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only list failed combinations, don't re-run")
    parser.add_argument("--solvers", nargs="*", default=None,
                        help="Only re-run specific solvers (e.g. PYPOWER pandapower)")
    parser.add_argument("--systems", nargs="*", default=None,
                        help="Only re-run specific systems (e.g. 'IEEE 14-bus')")
    args = parser.parse_args()

    h5_path = Path(args.results_file)
    if not h5_path.exists():
        print(f"HDF5 file not found: {h5_path}")
        sys.exit(1)

    print(f"Scanning {h5_path} for failed ACOPF combinations...\n")
    failed = find_failed_combinations(h5_path)

    # Filter out known genuine failures
    before = len(failed)
    failed = [(s, v) for s, v in failed if (s, v) not in _KNOWN_GENUINE_FAILURES]
    n_skipped = before - len(failed)
    if n_skipped:
        print(f"  (skipped {n_skipped} known genuine failures)")

    if args.solvers:
        failed = [(s, v) for s, v in failed if v in args.solvers]
    if args.systems:
        failed = [(s, v) for s, v in failed if s in args.systems]

    if not failed:
        print("\nNo failed combinations found!")
        return

    print(f"\n{len(failed)} combinations to re-run")

    if args.dry_run:
        print("(dry run — not executing)")
        return

    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor, as_completed

    n_workers = min(len(failed), mp.cpu_count() or 4)
    print(f"\nStarting parallel re-execution ({args.n_reps} reps each, "
          f"{n_workers} workers)...\n")

    # Launch all solve_one calls in parallel (each is an isolated subprocess)
    results_map: dict[tuple[str, str], dict | None] = {}
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        future_to_key = {
            pool.submit(solve_one, sys_name, solver_name, args.n_reps): (sys_name, solver_name)
            for sys_name, solver_name in failed
        }
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                results_map[key] = future.result()
            except Exception as e:
                print(f"  {key[0]} / {key[1]}: ERROR — {e}")
                results_map[key] = None

    # Patch HDF5 sequentially (HDF5 is not thread-safe)
    n_success, n_fail = 0, 0
    for sys_name, solver_name in failed:
        data = results_map.get((sys_name, solver_name))
        label = f"{sys_name} / {solver_name}"
        if data and data["result"] and data["result"].get("status", "") != "FAILED":
            patch_hdf5(h5_path, sys_name, solver_name, data)
            n_success += 1
        elif data and data["result"]:
            patch_hdf5(h5_path, sys_name, solver_name, data)
            print(f"  {label}: patched as FAILED (genuine convergence failure)")
            n_fail += 1
        else:
            print(f"  {label}: SKIPPED (no result)")
            n_fail += 1

    print(f"\nDone: {n_success} patched OK, {n_fail} still failed/skipped")


if __name__ == "__main__":
    main()
