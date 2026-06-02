#!/usr/bin/env python3
"""Integrated IEEE DCOPF validation: solve once, validate, and plot.

Solves IEEE/PEGASE bus systems (9, 14, 30, 57, 118, 300, 1354, 2869, 9241, 13659) using ESFEX's
Julia-backed DCOPF, validates against analytical reference solutions, and
generates paper-ready PDF figures — all in a single run.

Results are saved incrementally to an HDF5 file so that figures can be
regenerated without re-running the benchmark (``--plot-only``).

Usage:
    python tests/run_ieee_validation.py
    python tests/run_ieee_validation.py --output-dir /tmp/ieee_results
    python tests/run_ieee_validation.py --systems 9 14 30 57
    python tests/run_ieee_validation.py --sequential
    python tests/run_ieee_validation.py --plot-only
    python tests/run_ieee_validation.py --plot-only --results-file /tmp/bench.h5
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
from pathlib import Path

# Add project root to path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))
sys.path.insert(0, str(_PROJECT_ROOT))

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
from tests.ieee_benchmark_io import load_results, save_results
from tests.ieee_validation_plots import (
    _assemble_results,
    _build_task_list,
    _setup_paper_style,
    _solve_ieee_system,
    _solve_julia_all,
    _solve_single_task,
    plot_generation_dispatch,
    plot_line_flows,
    plot_solve_times,
    plot_ac_quantities,
    plot_solver_clustering,
    plot_voltage_angles,
    warmup_julia,
    # ACOPF solving infrastructure
    warmup_julia_acopf,
    _solve_julia_all_acopf,
    _build_acopf_task_list,
    _solve_acopf_single_task,
    _assemble_acopf_results,
)

# ── Available systems ────────────────────────────────────────────────────

_SYSTEM_LOADERS = {
    9: ("IEEE 9-bus", ieee_9bus),
    14: ("IEEE 14-bus", ieee_14bus),
    30: ("IEEE 30-bus", ieee_30bus),
    57: ("IEEE 57-bus", ieee_57bus),
    118: ("IEEE 118-bus", ieee_118bus),
    300: ("IEEE 300-bus", ieee_300bus),
    1354: ("PEGASE 1354-bus", pegase_1354bus),
    2869: ("PEGASE 2869-bus", pegase_2869bus),
    9241: ("PEGASE 9241-bus", pegase_9241bus),
    13659: ("PEGASE 13659-bus", pegase_13659bus),  # re-enabled: cost fix applied; angles degenerate (all rateA=0)
}


# ── Validation logic ────────────────────────────────────────────────────


def validate_system(result: dict) -> list[tuple[str, bool, str]]:
    """Run dispatch validation checks on a solved IEEE system.

    All solvers (including ESFEX) use the unified result format:
      angles_deg, line_flows_mw, gen_dispatch_list, gen_dispatch_mw, total_cost, status

    Returns list of (check_name, passed, detail_message) tuples.
    """
    checks: list[tuple[str, bool, str]] = []
    data = result["ieee_data"]
    sol = result.get("ESFEX", {})

    # 1. Solver optimal
    optimal = "OPTIMAL" in sol.get("status", "")
    checks.append(("optimal", optimal, sol.get("status", "unknown")))

    # 2. Generation equals demand
    gen_dispatch = sol.get("gen_dispatch_list", [])
    total_gen = sum(gen_dispatch)
    total_load = data["total_load_mw"]
    gen_ok = abs(total_gen - total_load) < 1.0
    checks.append((
        "gen=dem",
        gen_ok,
        f"gen={total_gen:.1f} load={total_load:.1f}",
    ))

    # 3. Slack bus angle zero
    slack_bus = data.get("slack_bus", 0)
    angles = sol.get("angles_deg", [])
    slack_angle = float(angles[slack_bus]) if slack_bus < len(angles) else 999.0
    slack_ok = abs(slack_angle) < 1e-4
    checks.append(("slack_angle", slack_ok, f"angle={slack_angle:.2e}"))

    # 4. Line flows within limits
    flows_ok = True
    flow_detail = ""
    flows_mw = sol.get("line_flows_mw", [])
    for idx, line in enumerate(data["lines"]):
        if idx < len(flows_mw):
            flow = abs(flows_mw[idx])
            limit = line["rate_mw"]
            if limit <= 0:
                continue  # unlimited (MATPOWER convention)
            if flow > limit + 0.5:
                flows_ok = False
                flow_detail = (
                    f"line {line['from']}-{line['to']}: "
                    f"{flow:.1f}>{limit:.1f}"
                )
                break
    if not flow_detail:
        flow_detail = "all OK"
    checks.append(("flows", flows_ok, flow_detail))

    # 5. Generation dispatch feasible (respects Pmin and Pmax)
    gen_feasible = True
    gen_detail = ""
    for g_idx, g in enumerate(data["generators"]):
        if g_idx < len(gen_dispatch):
            output = gen_dispatch[g_idx]
            pg_min = g.get("pg_min", 0.0)
            if output < pg_min - 0.5 or output > g["pg_max"] + 0.5:
                gen_feasible = False
                gen_detail = (
                    f"gen {g_idx} bus {g['bus']}: {output:.1f} "
                    f"vs [{pg_min:.1f}, {g['pg_max']:.1f}]"
                )
                break
    if not gen_detail:
        gen_detail = "all OK"
    checks.append(("gen_ok", gen_feasible, gen_detail))

    # 6. Cross-solver cost agreement
    # Collect all solver costs and compare against median
    all_costs = {}
    rfx_cost = sol.get("total_cost", 0.0)
    all_costs["ESFEX"] = rfx_cost
    for solver_name in ("PYPOWER", "scipy", "PyPSA", "pandapower", "PowerModels",
                        "GridCal", "Egret", "MATPOWER"):
        if solver_name in result:
            all_costs[solver_name] = result[solver_name]["total_cost"]

    costs_list = sorted(all_costs.values())
    median_cost = costs_list[len(costs_list) // 2]
    # 0.5% tolerance against median cost
    cost_match = True
    for name, cost in all_costs.items():
        if abs(cost - median_cost) > max(1.0, 0.005 * median_cost):
            cost_match = False
            break

    cost_detail = (f"med=${median_cost:,.0f} "
                   f"range=[${min(costs_list):,.0f}, ${max(costs_list):,.0f}] "
                   f"({len(all_costs)} solvers)")
    checks.append(("cost_agree", cost_match, cost_detail))

    return checks


def _print_summary(
    all_results: list[tuple[str, list[tuple[str, bool, str]]]],
) -> bool:
    """Print validation summary table. Returns True if all passed."""
    check_names = [c[0] for c in all_results[0][1]]
    header = f"{'System':<16}" + "".join(f"{n:>10}" for n in check_names) + "  Result"
    print()
    print("Validation Results")
    sep = "-" * len(header)
    print(sep)
    print(header)
    print(sep)

    all_pass = True
    for sys_name, checks in all_results:
        row = f"{sys_name:<16}"
        sys_pass = True
        for _, passed, _ in checks:
            mark = "PASS" if passed else "FAIL"
            row += f"{mark:>10}"
            if not passed:
                sys_pass = False
                all_pass = False
        row += f"  {'PASS' if sys_pass else '** FAIL **'}"
        print(row)
    print(sep)

    # Print details for failures
    for sys_name, checks in all_results:
        for name, passed, detail in checks:
            if not passed:
                print(f"  FAIL {sys_name} / {name}: {detail}")

    return all_pass


# ── Main ─────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="IEEE DCOPF validation: solve, validate, and plot.",
    )
    parser.add_argument(
        "--output-dir",
        default="results/ieee_validation",
        help="Output directory for PDF figures (default: results/ieee_validation)",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="Solve systems sequentially instead of in parallel",
    )
    parser.add_argument(
        "--systems",
        type=int,
        nargs="+",
        default=None,
        help="Bus counts to validate (e.g. --systems 9 14 30 57). Default: all",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=5,
        help="Number of repetitions per (system, solver) pair (default: 5)",
    )
    parser.add_argument(
        "--acopf",
        action="store_true",
        help="Also run ACOPF benchmark (requires Ipopt)",
    )
    parser.add_argument(
        "--acopf-repeats",
        type=int,
        default=3,
        help="Number of ACOPF reps per (system, solver) pair (default: 3)",
    )
    parser.add_argument(
        "--plot-only",
        action="store_true",
        help="Skip solving; load results from HDF5 and regenerate figures only",
    )
    parser.add_argument(
        "--results-file",
        type=str,
        default=None,
        help="Path to HDF5 results file (default: <output-dir>/benchmark_results.h5)",
    )
    args = parser.parse_args()

    output_path = Path(args.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # HDF5 results file
    results_file = Path(
        args.results_file
        if args.results_file
        else output_path / "benchmark_results.h5"
    )

    # ── Plot-only mode: load from HDF5 and skip solving ──
    if args.plot_only:
        print("IEEE Validation: Plot-Only Mode (loading from HDF5)")
        print("=" * 55)
        results = load_results(results_file, section="dc")
        acopf_results = load_results(results_file, section="acopf") or None
        if not results:
            print(f"No DC results found in {results_file}. Run the benchmark first.")
            sys.exit(1)

        # ── Generate figures ──
        print(f"\nGenerating figures in {output_path}/...")
        _setup_paper_style()
        plot_voltage_angles(results, output_path, acopf_results=acopf_results)
        plot_line_flows(results, output_path, acopf_results=acopf_results)
        plot_generation_dispatch(results, output_path, acopf_results=acopf_results)
        plot_solve_times(results, output_path, acopf_results=acopf_results)
        plot_solver_clustering(results, output_path, acopf_results=acopf_results)
        if acopf_results:
            plot_ac_quantities(acopf_results, output_path)
        print(f"\nAll figures saved to {output_path}/")
        print(f"  (from {results_file})")
        return

    # ── Full benchmark mode ──

    # Select systems
    if args.systems:
        selected = {}
        for s in args.systems:
            if s in _SYSTEM_LOADERS:
                selected[s] = _SYSTEM_LOADERS[s]
            else:
                available = sorted(_SYSTEM_LOADERS.keys())
                print(f"Unknown system: {s}-bus. Available: {available}")
                sys.exit(1)
    else:
        selected = _SYSTEM_LOADERS

    systems = [loader() for _, loader in selected.values()]

    print("IEEE Validation: Solve + Validate + Plot")
    print("=" * 42)

    # JIT warmup — compile Julia code before timing begins
    print("Warming up Julia JIT...", end=" ", flush=True)
    t_jit = time.perf_counter()
    warmup_julia()
    print(f"done ({time.perf_counter() - t_jit:.1f}s)")

    # ── Step 1: Solve ──
    t_total = time.perf_counter()

    n_reps = args.repeats

    if args.sequential or len(systems) == 1:
        print(f"Solving {len(systems)} system(s) sequentially ({n_reps} reps)...")
        results = [_solve_ieee_system(s, n_reps=n_reps) for s in systems]
    else:
        # Solve Julia solvers in main process (Julia is fork-unsafe)
        print(f"Solving Julia solvers (main process, {len(systems)} systems, {n_reps} reps)...")
        esfex_outputs = _solve_julia_all(systems, n_reps=n_reps)

        # Parallelize Python-only solvers
        tasks = _build_task_list(systems, n_reps=n_reps)
        n_workers = min(len(tasks), mp.cpu_count() or 4)
        n_py_solvers = len(tasks) // len(systems) if systems else 0
        print(f"Solving {len(tasks)} Python tasks in parallel "
              f"({len(systems)} systems x {n_py_solvers} solvers, "
              f"{n_workers} workers)...")
        with mp.Pool(processes=n_workers, maxtasksperchild=1) as pool:
            task_outputs = pool.map(_solve_single_task, tasks)
        results = _assemble_results(esfex_outputs + task_outputs, systems)

    elapsed = time.perf_counter() - t_total
    print(f"\nAll tasks solved in {elapsed:.1f}s")

    # Save DC results to HDF5
    save_results(results_file, results, section="dc")

    # ── Step 2: Validate ──
    all_results = []
    for r in results:
        checks = validate_system(r)
        all_results.append((r["name"], checks))

    all_pass = _print_summary(all_results)

    # ── Step 2b: Memory summary ──
    print("\nPeak Memory (MB delta RSS per solver per system)")
    # Collect all solver names across results
    all_solver_names = []
    for r in results:
        for s in r.get("peak_memory", {}):
            if s not in all_solver_names:
                all_solver_names.append(s)
    # Header
    hdr = f"{'System':<16}" + "".join(f"{s:>14}" for s in all_solver_names)
    print("-" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        mem = r.get("peak_memory", {})
        row = f"{r['name']:<16}"
        for s in all_solver_names:
            v = mem.get(s, float("nan"))
            row += f"{v:>14.1f}"
        print(row)
    print("-" * len(hdr))

    # Also print solve times summary
    print("\nMedian Solve Times (seconds)")
    hdr2 = f"{'System':<16}" + "".join(f"{s:>14}" for s in all_solver_names)
    print("-" * len(hdr2))
    print(hdr2)
    print("-" * len(hdr2))
    for r in results:
        times = r.get("solve_times", {})
        row = f"{r['name']:<16}"
        for s in all_solver_names:
            v = times.get(s, float("nan"))
            if v < 1.0:
                row += f"{v*1000:>13.1f}ms"
            else:
                row += f"{v:>14.2f}"
        print(row)
    print("-" * len(hdr2))

    # ── Step 3: ACOPF Benchmark (optional, before plotting) ──
    acopf_results = None
    if args.acopf:
        print("\n" + "=" * 50)
        print("ACOPF Benchmark")
        print("=" * 50)

        acopf_systems = list(systems)
        if not acopf_systems:
            print("No systems selected for ACOPF.")
        else:
            # JIT warmup for Ipopt
            print("Warming up Ipopt JIT...", end=" ", flush=True)
            t_jit = time.perf_counter()
            warmup_julia_acopf()
            print(f"done ({time.perf_counter() - t_jit:.1f}s)")

            n_ac_reps = args.acopf_repeats
            t_ac = time.perf_counter()

            # Julia ACOPF solvers in main process
            print(f"Solving Julia ACOPF ({len(acopf_systems)} systems, "
                  f"{n_ac_reps} reps)...")
            ac_julia = _solve_julia_all_acopf(acopf_systems, n_reps=n_ac_reps)

            # Python ACOPF solvers in parallel (fork works fine —
            # child processes only run Python solvers, no Julia calls)
            ac_tasks = _build_acopf_task_list(acopf_systems, n_reps=n_ac_reps)
            ac_task_out = []
            if ac_tasks:
                # Cap workers: ACOPF tasks are memory-intensive (PEGASE >1GB).
                # Too many workers causes transient numpy errors under numpy 2.x.
                n_ac_workers = min(len(ac_tasks), mp.cpu_count() or 4, 12)
                print(f"Solving {len(ac_tasks)} Python ACOPF tasks in parallel "
                      f"({n_ac_workers} workers)...")
                with mp.Pool(processes=n_ac_workers, maxtasksperchild=1) as pool:
                    ac_task_out = pool.map(_solve_acopf_single_task, ac_tasks)

            acopf_results = _assemble_acopf_results(
                ac_julia + ac_task_out, acopf_systems
            )

            elapsed_ac = time.perf_counter() - t_ac
            print(f"\nACOPF solved in {elapsed_ac:.1f}s")

            # Save ACOPF results to HDF5
            save_results(results_file, acopf_results, section="acopf")

            # ACOPF validation summary
            print("\nACOPF Results Summary")
            print("-" * 60)
            for r in acopf_results:
                converged = []
                for s in r:
                    if isinstance(r[s], dict) and "status" in r[s]:
                        st = r[s]["status"]
                        cost = r[s].get("total_cost", 0)
                        tag = "OK" if "OPTIMAL" in st else st
                        converged.append(f"{s}={tag}(${cost:,.0f})")
                print(f"  {r['name']}: {', '.join(converged)}")
            print("-" * 60)

    # ── Step 4: Plot (integrated DC + AC) ──
    print(f"\nGenerating figures in {output_path}/...")
    _setup_paper_style()
    plot_voltage_angles(results, output_path, acopf_results=acopf_results)
    plot_line_flows(results, output_path, acopf_results=acopf_results)
    plot_generation_dispatch(results, output_path, acopf_results=acopf_results)
    plot_solve_times(results, output_path, acopf_results=acopf_results)
    plot_solver_clustering(results, output_path, acopf_results=acopf_results)
    if acopf_results:
        plot_ac_quantities(acopf_results, output_path)
    print(f"\nAll figures saved to {output_path}/")

    # ── Exit ──
    if all_pass:
        print(f"\nAll {len(results)} systems PASSED DCOPF validation.")
    else:
        print(f"\nSome systems FAILED DCOPF validation.")
        sys.exit(1)


if __name__ == "__main__":
    main()
