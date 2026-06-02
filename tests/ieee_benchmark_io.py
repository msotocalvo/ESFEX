"""HDF5 serialization for IEEE benchmark results.

Saves / loads the ``results`` and ``acopf_results`` lists produced by
``run_ieee_validation.py`` so that figures can be regenerated without
re-running the full benchmark.

Usage::

    # After solving
    save_results(path, results, section="dc")
    save_results(path, acopf_results, section="acopf")

    # Later, for plotting only
    results = load_results(path, section="dc")
    acopf_results = load_results(path, section="acopf")
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import h5py
import numpy as np

# ── System name → ieee_data loader mapping ──────────────────────────────

_NAME_TO_LOADER: dict[str, callable] | None = None


def _get_loader_map() -> dict[str, callable]:
    """Lazily build a map from system display name to fixture loader."""
    global _NAME_TO_LOADER
    if _NAME_TO_LOADER is not None:
        return _NAME_TO_LOADER

    from tests.fixtures.ieee_bus_data import (
        ieee_9bus,
        ieee_14bus,
        ieee_30bus,
        ieee_57bus,
        ieee_118bus,
        ieee_300bus,
        pegase_1354bus,
        pegase_2869bus,
        pegase_9241bus,
        pegase_13659bus,
    )

    _NAME_TO_LOADER = {
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
    return _NAME_TO_LOADER


# ── Meta-keys that are NOT solver results ───────────────────────────────

_META_KEYS = frozenset({
    "name", "ieee_data",
    "solve_times", "solve_times_all", "solve_times_iqr",
    "solver_times", "build_times", "peak_memory",
})


# ── Save ────────────────────────────────────────────────────────────────


def _write_solver_result(grp: h5py.Group, result: dict) -> None:
    """Write a single solver result dict into an HDF5 group."""
    grp.attrs["status"] = result.get("status", "UNKNOWN")
    grp.attrs["total_cost"] = float(result.get("total_cost", 0.0))

    # Numerical arrays
    _ARRAY_KEYS = [
        "angles_deg", "line_flows_mw", "gen_dispatch_list",
        # ACOPF-specific
        "vm_pu", "line_flows_mvar", "line_flows_to_mw",
        "gen_reactive_list",
    ]
    for key in _ARRAY_KEYS:
        if key in result:
            data = np.asarray(result[key], dtype=np.float64)
            grp.create_dataset(key, data=data)

    # gen_dispatch_mw: dict {bus_idx: mw} → two parallel arrays
    gdm = result.get("gen_dispatch_mw")
    if gdm:
        keys = sorted(gdm.keys())
        grp.create_dataset("_gen_bus_keys", data=np.array(keys, dtype=np.int64))
        grp.create_dataset("_gen_bus_vals", data=np.array(
            [gdm[k] for k in keys], dtype=np.float64))

    # Internal solver time (scalar)
    if "_solver_time" in result:
        grp.attrs["_solver_time"] = float(result["_solver_time"])


def _write_timing(grp: h5py.Group, sys_result: dict) -> None:
    """Write timing / memory data into a timing/ group."""
    # solve_times_all: {solver: [dt1, dt2, ...]}
    sta = sys_result.get("solve_times_all", {})
    if sta:
        sta_grp = grp.create_group("solve_times_all")
        for solver, dt_list in sta.items():
            sta_grp.create_dataset(solver, data=np.array(dt_list, dtype=np.float64))

    # peak_memory: {solver: mb}
    pm = sys_result.get("peak_memory", {})
    if pm:
        pm_grp = grp.create_group("peak_memory")
        for solver, mb in pm.items():
            pm_grp.create_dataset(solver, data=float(mb))

    # solver_times (internal): {solver: seconds}
    st = sys_result.get("solver_times", {})
    if st:
        st_grp = grp.create_group("solver_times")
        for solver, sec in st.items():
            st_grp.create_dataset(solver, data=float(sec))


def save_results(
    path: str | Path,
    results: list[dict],
    section: str = "dc",
) -> None:
    """Save benchmark results to HDF5 (incremental, per-system).

    Parameters
    ----------
    path : path to the HDF5 file (created if needed, opened in append mode)
    results : list of per-system result dicts (as produced by _assemble_results)
    section : "dc" or "acopf"
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with h5py.File(path, "a") as f:
        # Root metadata (updated on every write)
        f.attrs["last_updated"] = datetime.now().isoformat()

        sec = f.require_group(section)

        for sys_result in results:
            sys_name = sys_result["name"]

            # Overwrite if system already exists
            if sys_name in sec:
                del sec[sys_name]
            sg = sec.create_group(sys_name)
            sg.attrs["name"] = sys_name

            # Solver results (keys not in _META_KEYS)
            for key, val in sys_result.items():
                if key in _META_KEYS or not isinstance(val, dict):
                    continue
                # Must look like a solver result (has "status" or array keys)
                if "status" not in val and "angles_deg" not in val:
                    continue
                _write_solver_result(sg.create_group(key), val)

            # Timing and memory
            _write_timing(sg.create_group("timing"), sys_result)

    print(f"  Saved {section} results ({len(results)} systems) → {path}")


# ── Load ────────────────────────────────────────────────────────────────


def _read_solver_result(grp: h5py.Group) -> dict:
    """Read a solver result dict from an HDF5 group."""
    result: dict = {
        "status": str(grp.attrs.get("status", "UNKNOWN")),
        "total_cost": float(grp.attrs.get("total_cost", 0.0)),
    }

    # Numerical arrays
    for key in grp:
        if key.startswith("_"):
            continue
        ds = grp[key]
        if isinstance(ds, h5py.Dataset):
            result[key] = ds[:].tolist()

    # Reconstruct gen_dispatch_mw
    if "_gen_bus_keys" in grp and "_gen_bus_vals" in grp:
        keys = grp["_gen_bus_keys"][:].tolist()
        vals = grp["_gen_bus_vals"][:].tolist()
        result["gen_dispatch_mw"] = {int(k): float(v) for k, v in zip(keys, vals)}

    # Internal solver time
    if "_solver_time" in grp.attrs:
        result["_solver_time"] = float(grp.attrs["_solver_time"])

    return result


def _read_timing(grp: h5py.Group) -> dict:
    """Read timing data from a timing/ group and derive all timing dicts."""
    solve_times: dict[str, float] = {}
    solve_times_all: dict[str, list[float]] = {}
    solve_times_iqr: dict[str, tuple[float, float]] = {}
    solver_times: dict[str, float] = {}
    build_times: dict[str, float] = {}
    peak_memory: dict[str, float] = {}

    # solve_times_all → derive solve_times and solve_times_iqr
    sta_grp = grp.get("solve_times_all")
    if sta_grp:
        for solver in sta_grp:
            ds = sta_grp[solver]
            dt_list = ds[:].tolist() if ds.shape else [float(ds[()])]
            solve_times_all[solver] = dt_list
            solve_times[solver] = float(np.median(dt_list))
            solve_times_iqr[solver] = (
                float(np.percentile(dt_list, 25)),
                float(np.percentile(dt_list, 75)),
            )

    # peak_memory
    pm_grp = grp.get("peak_memory")
    if pm_grp:
        for solver in pm_grp:
            peak_memory[solver] = float(pm_grp[solver][()])

    # solver_times (internal) → derive build_times
    st_grp = grp.get("solver_times")
    if st_grp:
        for solver in st_grp:
            st = float(st_grp[solver][()])
            solver_times[solver] = st
            if solver in solve_times:
                build_times[solver] = max(0.0, solve_times[solver] - st)

    return {
        "solve_times": solve_times,
        "solve_times_all": solve_times_all,
        "solve_times_iqr": solve_times_iqr,
        "solver_times": solver_times,
        "build_times": build_times,
        "peak_memory": peak_memory,
    }


def load_results(
    path: str | Path,
    section: str = "dc",
) -> list[dict]:
    """Load benchmark results from HDF5.

    Returns a list of per-system dicts in the same format as
    ``_assemble_results()`` / ``_assemble_acopf_results()``, with
    ``ieee_data`` reloaded from the fixture loaders.

    Parameters
    ----------
    path : path to the HDF5 file
    section : "dc" or "acopf"
    """
    path = Path(path)
    if not path.exists():
        return []

    loaders = _get_loader_map()
    results: list[dict] = []

    with h5py.File(path, "r") as f:
        sec = f.get(section)
        if sec is None:
            return []

        for sys_name in sec:
            sg = sec[sys_name]
            name = str(sg.attrs.get("name", sys_name))

            # Reload ieee_data from fixtures
            loader = loaders.get(name)
            if loader is None:
                print(f"  Warning: no loader for '{name}', skipping")
                continue
            ieee_data = loader()

            entry: dict = {
                "name": name,
                "ieee_data": ieee_data,
            }

            # Backward compat: old files stored PYPOWER under "reference"
            if "reference" in sg:
                entry["PYPOWER"] = _read_solver_result(sg["reference"])

            # Read solver results (any subgroup that isn't "reference" or "timing")
            for key in sg:
                if key in ("reference", "timing"):
                    continue
                sub = sg[key]
                if isinstance(sub, h5py.Group):
                    entry[key] = _read_solver_result(sub)

            # Read timing
            if "timing" in sg:
                entry.update(_read_timing(sg["timing"]))
            else:
                entry.update({
                    "solve_times": {}, "solve_times_all": {},
                    "solve_times_iqr": {}, "solver_times": {},
                    "build_times": {}, "peak_memory": {},
                })

            results.append(entry)

    # Sort by system size (num_buses) for consistent ordering
    results.sort(key=lambda r: r["ieee_data"].get("num_buses", 0))

    print(f"  Loaded {section} results ({len(results)} systems) ← {path}")
    return results
