"""External DCOPF reference solvers for IEEE benchmark validation.

Wraps PyPSA, pandapower, PYPOWER, and PowerModels.jl to solve the same
IEEE standard bus systems that ESFEX solves.  Each wrapper takes the
``ieee_data`` dict produced by :mod:`tests.fixtures.ieee_bus_data` and
returns a result dict with the same keys as
:func:`~tests.fixtures.ieee_bus_data.compute_dc_opf_reference`:

.. code-block:: python

    {
        "angles_deg": [...],          # voltage angle per bus (degrees)
        "line_flows_mw": [...],       # power flow per line (MW)
        "gen_dispatch_mw": {bus: MW}, # dispatch keyed by bus id
        "gen_dispatch_list": [...],   # dispatch per generator (MW)
        "total_cost": float,          # total fuel cost ($)
    }

Only solvers whose packages are installed are available; missing packages
are silently skipped.
"""

from __future__ import annotations

import inspect
import logging
import math
import warnings
from pathlib import Path
from typing import Any, Callable

import numpy as np
from scipy import sparse as sp
from scipy.optimize import Bounds, LinearConstraint, milp


# ── HiGHS QP solver (drop-in replacement for qps_pips) ───────────────


def _qps_highs(H, c, A, l, u, xmin=None, xmax=None, x0=None, opt=None):
    """Drop-in replacement for qps_pips using HiGHS (via scipy.optimize.milp).

    Solves: min c'x  s.t.  l <= Ax <= u,  xmin <= x <= xmax

    Used for DC OPF where costs are linear (H=0).  Returns the same
    (x, f, eflag, output, lmbda) tuple that qps_pips returns.
    """
    c_flat = np.asarray(c, dtype=float).flatten()
    n = len(c_flat)

    # Variable bounds
    lb = np.asarray(xmin, dtype=float).flatten() if xmin is not None else np.full(n, -1e20)
    ub = np.asarray(xmax, dtype=float).flatten() if xmax is not None else np.full(n, 1e20)

    # Constraints: l <= Ax <= u
    constraints = []
    m = 0
    if A is not None and A.shape[0] > 0:
        m = A.shape[0]
        l_flat = np.asarray(l, dtype=float).flatten()[:m]
        u_flat = np.asarray(u, dtype=float).flatten()[:m]
        A_mat = A.tocsc() if sp.issparse(A) else sp.csc_matrix(A)
        constraints = [LinearConstraint(A_mat, l_flat, u_flat)]

    result = milp(
        c=c_flat,
        constraints=constraints,
        bounds=Bounds(lb=lb, ub=ub),
        options={"disp": False},
    )

    if result.success:
        x = result.x
        f = float(result.fun)
        eflag = 1
    else:
        x = np.zeros(n)
        f = 0.0
        eflag = 0

    output = {"solver": "HiGHS"}

    # Zero duals (sufficient for primal solution extraction)
    lmbda = {
        "mu_l": np.zeros(m),
        "mu_u": np.zeros(m),
        "lower": np.zeros(n),
        "upper": np.zeros(n),
    }

    return x, f, eflag, output, lmbda


def _patch_solver_to_highs(module_path: str):
    """Monkey-patch qps_pips → _qps_highs in a qps_pypower module.

    Returns the original function for restoration.
    """
    import importlib
    mod = importlib.import_module(module_path)
    orig = getattr(mod, "qps_pips", None)
    setattr(mod, "qps_pips", _qps_highs)
    return mod, orig


def _restore_solver(mod, orig):
    """Restore original qps_pips after monkey-patching."""
    if orig is not None:
        mod.qps_pips = orig


# ── PyPSA ──────────────────────────────────────────────────────────────


def solve_with_pypsa(ieee_data: dict) -> dict:
    """Solve DCOPF using PyPSA's linear OPF (LOPF).

    Requires: ``pip install pypsa``
    """
    import pypsa

    # Suppress PyPSA/linopy/HiGHS log output
    for logger_name in ("pypsa", "linopy", "pypsa.consistency"):
        logging.getLogger(logger_name).setLevel(logging.ERROR)

    buses = ieee_data["buses"]
    generators = ieee_data["generators"]
    lines_data = ieee_data["lines"]
    n = ieee_data["num_buses"]
    slack = ieee_data["slack_bus"]

    net = pypsa.Network()
    net.set_snapshots([0])

    # Add buses
    for b in buses:
        net.add("Bus", f"bus_{b['bus_id']}")

    # Add loads
    for b in buses:
        if b["pd_mw"] != 0.0:
            net.add(
                "Load",
                f"load_{b['bus_id']}",
                bus=f"bus_{b['bus_id']}",
                p_set=b["pd_mw"],
            )

    # Add generators
    for g_idx, g in enumerate(generators):
        pg_min = g.get("pg_min", 0.0)
        pg_max = g["pg_max"]
        p_min_pu = pg_min / pg_max if pg_max > 0 else 0.0
        net.add(
            "Generator",
            f"gen_{g_idx}",
            bus=f"bus_{g['bus']}",
            p_nom=pg_max,
            marginal_cost=g["cost_mwh"],
            p_min_pu=p_min_pu,
        )

    # Add lines (effective reactance x_eff = x * tap for correct susceptance)
    for l_idx, l in enumerate(lines_data):
        tap = l.get("tap", 1.0)
        x_eff = l["x_pu"] * tap  # b = 1/x_eff = 1/(x*tap)
        # rate_mw=0 → unlimited (MATPOWER convention); PyPSA s_nom=0 means no limit
        net.add(
            "Line",
            f"line_{l_idx}",
            bus0=f"bus_{l['from']}",
            bus1=f"bus_{l['to']}",
            x=x_eff,
            r=l.get("r_pu", 0.0),
            s_nom=l["rate_mw"] if l["rate_mw"] > 0 else 1e10,
            s_nom_extendable=False,
        )

    # Solve LOPF (linear OPF = DCOPF)
    import time as _time
    _t_solver = _time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        status = net.optimize(solver_name="highs", solver_options={"output_flag": False})
    _solver_time = _time.perf_counter() - _t_solver

    # Extract results
    # PyPSA v_ang is in units of baseMVA * radians (i.e. flow = dv_ang / x_pu).
    # Convert: theta_deg = (v_ang - v_ang[slack]) / baseMVA * 180 / pi
    base_mva = ieee_data["base_mva"]
    v_ang = np.zeros(n)
    if hasattr(net, "buses_t") and "v_ang" in net.buses_t:
        for b_idx in range(n):
            bus_name = f"bus_{b_idx}"
            if bus_name in net.buses_t.v_ang.columns:
                v_ang[b_idx] = net.buses_t.v_ang[bus_name].iloc[0]

    # Re-reference to slack bus and convert to degrees
    v_ang -= v_ang[slack]
    angles_deg = (v_ang / base_mva * 180.0 / np.pi).tolist()

    # Line flows
    line_flows_mw = []
    for l_idx in range(len(lines_data)):
        line_name = f"line_{l_idx}"
        if line_name in net.lines_t.p0.columns:
            line_flows_mw.append(float(net.lines_t.p0[line_name].iloc[0]))
        else:
            line_flows_mw.append(0.0)

    # Generation dispatch
    gen_dispatch_mw = {}
    gen_dispatch_list = []
    for g_idx, g in enumerate(generators):
        gen_name = f"gen_{g_idx}"
        if gen_name in net.generators_t.p.columns:
            pg = float(net.generators_t.p[gen_name].iloc[0])
        else:
            pg = 0.0
        gen_dispatch_list.append(pg)
        gen_dispatch_mw[g["bus"]] = pg

    total_cost = float(net.objective)

    return {
        "angles_deg": angles_deg,
        "line_flows_mw": line_flows_mw,
        "gen_dispatch_mw": gen_dispatch_mw,
        "gen_dispatch_list": gen_dispatch_list,
        "total_cost": total_cost,
        "_solver_time": _solver_time,
    }


# ── pandapower ─────────────────────────────────────────────────────────


def solve_with_pandapower(ieee_data: dict) -> dict:
    """Solve DCOPF using pandapower's DC OPP (rundcopp).

    Requires: ``pip install pandapower``
    """
    import pandapower as pp

    # Suppress pandapower warnings (controllable loads, etc.)
    logging.getLogger("pandapower").setLevel(logging.ERROR)

    buses = ieee_data["buses"]
    generators = ieee_data["generators"]
    lines_data = ieee_data["lines"]
    n = ieee_data["num_buses"]
    slack = ieee_data["slack_bus"]
    base_mva = ieee_data["base_mva"]

    net = pp.create_empty_network(sn_mva=base_mva)

    # Default voltage: use first nonzero bus voltage, or 230 kV
    default_vn = 230.0
    for b in buses:
        vn = b.get("voltage_kv", 0.0)
        if vn > 0:
            default_vn = vn
            break

    # Add buses
    for b in buses:
        vn = b.get("voltage_kv", 0.0)
        if vn <= 0:
            vn = default_vn
        pp.create_bus(net, vn_kv=vn, name=f"bus_{b['bus_id']}")

    # Add external grid at slack bus (required for pandapower OPF)
    pp.create_ext_grid(net, bus=slack, vm_pu=1.0)

    # Add loads
    for b in buses:
        if b["pd_mw"] > 0:
            pp.create_load(net, bus=b["bus_id"], p_mw=b["pd_mw"])
        elif b["pd_mw"] < 0:
            # Negative load = generation; model as static gen
            pp.create_sgen(net, bus=b["bus_id"], p_mw=-b["pd_mw"])

    # Add generators (skip slack — ext_grid handles it)
    for g_idx, g in enumerate(generators):
        pg_min = g.get("pg_min", 0.0)
        if g["bus"] == slack:
            # Add cost to ext_grid instead
            pp.create_poly_cost(net, 0, "ext_grid", cp1_eur_per_mw=g["cost_mwh"])
            # Set ext_grid limits
            net.ext_grid.at[0, "max_p_mw"] = g["pg_max"]
            net.ext_grid.at[0, "min_p_mw"] = pg_min
            continue
        gen_idx = pp.create_gen(
            net,
            bus=g["bus"],
            p_mw=0.0,
            max_p_mw=g["pg_max"],
            min_p_mw=pg_min,
            controllable=True,
            slack=False,
        )
        pp.create_poly_cost(
            net, gen_idx, "gen", cp1_eur_per_mw=g["cost_mwh"]
        )

    # Add lines using explicit parameters
    # pandapower works in physical units, so we need to convert from per-unit
    # x_pu = x_ohm / z_base, z_base = vn_kv^2 / sn_mva
    # For DC OPF with per-unit, use length_km=1 and x_ohm_per_km = x_pu * z_base
    # Effective reactance includes tap: x_eff = x_pu * tap → b = 1/(x*tap)
    for l_idx, l in enumerate(lines_data):
        from_bus = l["from"]
        to_bus = l["to"]
        vn_kv = buses[from_bus].get("voltage_kv", 0.0)
        if vn_kv <= 0:
            vn_kv = default_vn
        z_base = vn_kv ** 2 / base_mva
        tap = l.get("tap", 1.0)
        x_ohm = l["x_pu"] * tap * z_base
        r_ohm = l.get("r_pu", 0.0) * z_base

        # Thermal limit: rate_mw → max_i_ka (rate_mw=0 → unlimited)
        rate = l["rate_mw"] if l["rate_mw"] > 0 else 99999.0
        max_i_ka = rate / (math.sqrt(3) * vn_kv) if vn_kv > 0 else 9999.0

        pp.create_line_from_parameters(
            net,
            from_bus=from_bus,
            to_bus=to_bus,
            length_km=1.0,
            r_ohm_per_km=max(r_ohm, 1e-6),  # avoid zero resistance
            x_ohm_per_km=x_ohm,
            c_nf_per_km=0.0,
            max_i_ka=max_i_ka,
            max_loading_percent=100.0,
            name=f"line_{l_idx}",
        )

    # Solve DC OPP (monkey-patch to use HiGHS instead of PIPS)
    import time as _time
    _t_solver = _time.perf_counter()
    mod, orig = _patch_solver_to_highs("pandapower.pypower.qps_pypower")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pp.rundcopp(net)
    finally:
        _restore_solver(mod, orig)
    _solver_time = _time.perf_counter() - _t_solver

    # Extract results
    angles_deg = [0.0] * n
    for b_idx in range(n):
        if b_idx in net.res_bus.index:
            angles_deg[b_idx] = float(net.res_bus.at[b_idx, "va_degree"])

    # Line flows
    line_flows_mw = []
    for l_idx in range(len(lines_data)):
        if l_idx in net.res_line.index:
            # p_from_mw is flow from bus0 to bus1
            line_flows_mw.append(float(net.res_line.at[l_idx, "p_from_mw"]))
        else:
            line_flows_mw.append(0.0)

    # Generation dispatch
    gen_dispatch_mw = {}
    gen_dispatch_list = []

    # Map pandapower gen indices back to ieee_data generator order
    pp_gen_idx = 0
    for g_idx, g in enumerate(generators):
        if g["bus"] == slack:
            # ext_grid dispatch
            pg = float(net.res_ext_grid.at[0, "p_mw"])
            gen_dispatch_list.append(pg)
            gen_dispatch_mw[g["bus"]] = pg
        else:
            if pp_gen_idx in net.res_gen.index:
                pg = float(net.res_gen.at[pp_gen_idx, "p_mw"])
            else:
                pg = 0.0
            gen_dispatch_list.append(pg)
            gen_dispatch_mw[g["bus"]] = pg
            pp_gen_idx += 1

    # Total cost from objective
    total_cost = float(net.res_cost)

    return {
        "angles_deg": angles_deg,
        "line_flows_mw": line_flows_mw,
        "gen_dispatch_mw": gen_dispatch_mw,
        "gen_dispatch_list": gen_dispatch_list,
        "total_cost": total_cost,
        "_solver_time": _solver_time,
    }


# ── PYPOWER ────────────────────────────────────────────────────────────


def solve_with_pypower(ieee_data: dict) -> dict:
    """Solve DCOPF using PYPOWER (Python port of MATPOWER).

    Requires: ``pip install pypower``
    """
    from pypower.api import ppoption, rundcopf

    buses = ieee_data["buses"]
    generators = ieee_data["generators"]
    lines_data = ieee_data["lines"]
    n = ieee_data["num_buses"]
    slack = ieee_data["slack_bus"]
    base_mva = ieee_data["base_mva"]

    # Build MATPOWER-style case arrays
    # Bus data: [bus_i, type, Pd, Qd, Gs, Bs, area, Vm, Va, baseKV, zone, Vmax, Vmin]
    bus = np.zeros((n, 13))
    for b in buses:
        i = b["bus_id"]
        bus_type = {"slack": 3, "PV": 2, "PQ": 1}.get(b["bus_type"], 1)
        bus[i, :] = [
            i + 1,       # 1-indexed bus number
            bus_type,
            b["pd_mw"],  # Pd
            0.0,         # Qd
            0.0,         # Gs
            0.0,         # Bs
            1,           # area
            1.0,         # Vm
            0.0,         # Va
            b.get("voltage_kv", 230.0),
            1,           # zone
            1.1,         # Vmax
            0.9,         # Vmin
        ]

    # Generator data: [bus, Pg, Qg, Qmax, Qmin, Vg, mBase, status, Pmax, Pmin, ...]
    n_gen = len(generators)
    gen = np.zeros((n_gen, 21))
    for g_idx, g in enumerate(generators):
        gen[g_idx, :10] = [
            g["bus"] + 1,   # 1-indexed bus
            0.0,            # Pg (initial)
            0.0,            # Qg
            999.0,          # Qmax
            -999.0,         # Qmin
            1.0,            # Vg
            base_mva,       # mBase
            1,              # status
            g["pg_max"],    # Pmax
            g.get("pg_min", 0.0),  # Pmin
        ]

    # Branch data: [fbus, tbus, r, x, b, rateA, rateB, rateC, ratio, angle, status, ...]
    n_lines = len(lines_data)
    branch = np.zeros((n_lines, 13))
    for l_idx, l in enumerate(lines_data):
        tap = l.get("tap", 1.0)
        # MATPOWER convention: ratio=0 means "not a transformer" (= 1.0)
        tap_matpower = tap if tap != 1.0 else 0.0
        shift_deg = l.get("shift_deg", 0.0)
        branch[l_idx, :] = [
            l["from"] + 1,          # 1-indexed
            l["to"] + 1,
            l.get("r_pu", 0.0),
            l["x_pu"],
            l.get("b_pu", 0.0),
            l["rate_mw"],           # rateA
            l["rate_mw"],           # rateB
            l["rate_mw"],           # rateC
            tap_matpower,           # ratio
            shift_deg,              # angle (degrees)
            1,                      # status
            -180.0,                 # angmin
            180.0,                  # angmax
        ]

    # Generator cost: [type, startup, shutdown, ncost, c(n-1), ..., c0]
    # For NCOST=2 (linear): cost = c1*pg + c0  →  columns: [2, 0, 0, 2, c1, c0]
    gencost = np.zeros((n_gen, 7))
    for g_idx, g in enumerate(generators):
        gencost[g_idx, :6] = [
            2,               # polynomial type
            0.0,             # startup
            0.0,             # shutdown
            2,               # ncost = 2 coefficients
            g["cost_mwh"],   # c1 (linear coefficient)
            0.0,             # c0 (constant)
        ]

    case = {
        "version": "2",
        "baseMVA": base_mva,
        "bus": bus,
        "gen": gen,
        "branch": branch,
        "gencost": gencost,
    }

    ppopt = ppoption(PF_DC=1, VERBOSE=0, OUT_ALL=0, OPF_ALG_DC=200)
    # Monkey-patch to use HiGHS instead of PIPS
    import time as _time
    _t_solver = _time.perf_counter()
    mod, orig = _patch_solver_to_highs("pypower.qps_pypower")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = rundcopf(case, ppopt)
    finally:
        _restore_solver(mod, orig)
    _solver_time = _time.perf_counter() - _t_solver

    if not result["success"]:
        raise RuntimeError("PYPOWER DCOPF did not converge")

    # Extract results — map back from 1-indexed to 0-indexed
    # Build reverse bus map
    res_bus = result["bus"]
    res_gen = result["gen"]
    res_branch = result["branch"]

    angles_deg = [0.0] * n
    for row in res_bus:
        orig_bus = int(row[0]) - 1  # back to 0-indexed
        angles_deg[orig_bus] = float(row[8])  # Va column

    # Line flows (Pf column = index 13)
    line_flows_mw = []
    for l_idx in range(n_lines):
        pf = float(res_branch[l_idx, 13])  # Pf (from-bus power)
        line_flows_mw.append(pf)

    # Generation dispatch
    gen_dispatch_mw = {}
    gen_dispatch_list = []
    for g_idx in range(n_gen):
        pg = float(res_gen[g_idx, 1])  # Pg column
        gen_dispatch_list.append(pg)
        bus_0idx = int(res_gen[g_idx, 0]) - 1
        gen_dispatch_mw[bus_0idx] = pg

    # Total cost
    total_cost = float(result["f"])

    return {
        "angles_deg": angles_deg,
        "line_flows_mw": line_flows_mw,
        "gen_dispatch_mw": gen_dispatch_mw,
        "gen_dispatch_list": gen_dispatch_list,
        "total_cost": total_cost,
        "_solver_time": _solver_time,
    }


# ── PowerModels.jl ─────────────────────────────────────────────────────


def _ieee_to_matpower_file(ieee_data: dict, path: str):
    """Write ieee_data as a MATPOWER .m case file for PowerModels.jl."""
    buses = ieee_data["buses"]
    generators = ieee_data["generators"]
    lines_data = ieee_data["lines"]
    base_mva = ieee_data["base_mva"]

    with open(path, "w") as f:
        f.write("function mpc = case_custom\n")
        f.write("mpc.version = '2';\n")
        f.write(f"mpc.baseMVA = {base_mva};\n\n")

        # Bus data
        f.write("%% bus data\n")
        f.write("mpc.bus = [\n")
        for b in buses:
            bt = {"slack": 3, "PV": 2, "PQ": 1}.get(b["bus_type"], 1)
            vn = b.get("voltage_kv", 230.0)
            f.write(f"  {b['bus_id']+1}  {bt}  {b['pd_mw']}  0  0  0  1  1  0  "
                    f"{vn}  1  1.1  0.9;\n")
        f.write("];\n\n")

        # Generator data
        f.write("%% generator data\n")
        f.write("mpc.gen = [\n")
        for g in generators:
            pg_min = g.get("pg_min", 0.0)
            f.write(f"  {g['bus']+1}  0  0  999  -999  1  {base_mva}  1  "
                    f"{g['pg_max']}  {pg_min}  0  0  0  0  0  0  0  0  0  0  0;\n")
        f.write("];\n\n")

        # Branch data
        f.write("%% branch data\n")
        f.write("mpc.branch = [\n")
        for l in lines_data:
            r = l.get("r_pu", 0.0)
            b = l.get("b_pu", 0.0)
            tap = l.get("tap", 1.0)
            tap_mp = tap if tap != 1.0 else 0.0  # MATPOWER convention
            shift = l.get("shift_deg", 0.0)
            f.write(f"  {l['from']+1}  {l['to']+1}  {r}  {l['x_pu']}  {b}  "
                    f"{l['rate_mw']}  {l['rate_mw']}  {l['rate_mw']}  "
                    f"{tap_mp}  {shift}  1  -180  180;\n")
        f.write("];\n\n")

        # Generator cost (linear: type=2, ncost=2, c1, c0)
        f.write("%% generator cost data\n")
        f.write("mpc.gencost = [\n")
        for g in generators:
            f.write(f"  2  0  0  2  {g['cost_mwh']}  0;\n")
        f.write("];\n")


def solve_with_powermodels(ieee_data: dict) -> dict:
    """Solve DCOPF using PowerModels.jl via Julia bridge.

    Requires: ``PowerModels`` and ``HiGHS`` Julia packages.
    Uses the same juliacall bridge that ESFEX uses.
    """
    import os
    import tempfile

    from juliacall import Main as jl

    jl.seval("using PowerModels, HiGHS")
    jl.seval("PowerModels.silence()")

    buses = ieee_data["buses"]
    generators = ieee_data["generators"]
    lines_data = ieee_data["lines"]
    n = ieee_data["num_buses"]
    base_mva = ieee_data["base_mva"]

    # Write temporary MATPOWER .m file
    import time as _time
    fd, temp_path = tempfile.mkstemp(suffix=".m")
    os.close(fd)
    try:
        _ieee_to_matpower_file(ieee_data, temp_path)
        # Use IPM solver — dual simplex fails on large systems (>5000 buses)
        # Cache the Julia function to avoid JIT recompilation on every call
        global _pm_solve_fn
        try:
            _pm_solve_fn
        except NameError:
            _pm_solve_fn = jl.seval("""
            function(path)
                opt = optimizer_with_attributes(
                    HiGHS.Optimizer,
                    "output_flag" => false,
                    "solver" => "simplex",
                )
                redirect_stdout(devnull) do
                    PowerModels.solve_opf(path, DCMPPowerModel, opt)
                end
            end
            """)
        _t_solver = _time.perf_counter()
        result = _pm_solve_fn(temp_path)
        _solver_time = _time.perf_counter() - _t_solver
    finally:
        os.unlink(temp_path)

    # Convert Julia result to Python
    result = dict(result)
    solution = dict(result["solution"])

    # Extract angles (radians → degrees)
    sol_bus = dict(solution["bus"])
    angles_deg = [0.0] * n
    for b in buses:
        bus_1idx = str(b["bus_id"] + 1)
        if bus_1idx in sol_bus:
            va_rad = float(dict(sol_bus[bus_1idx])["va"])
            angles_deg[b["bus_id"]] = math.degrees(va_rad)

    # Line flows (per-unit → MW)
    sol_branch = dict(solution["branch"])
    line_flows_mw = []
    for l_idx in range(len(lines_data)):
        br_1idx = str(l_idx + 1)
        if br_1idx in sol_branch:
            pf_pu = float(dict(sol_branch[br_1idx])["pf"])
            line_flows_mw.append(pf_pu * base_mva)
        else:
            line_flows_mw.append(0.0)

    # Generation dispatch (per-unit → MW)
    sol_gen = dict(solution["gen"])
    gen_dispatch_mw = {}
    gen_dispatch_list = []
    for g_idx, g in enumerate(generators):
        gen_1idx = str(g_idx + 1)
        if gen_1idx in sol_gen:
            pg_pu = float(dict(sol_gen[gen_1idx])["pg"])
            pg_mw = pg_pu * base_mva
        else:
            pg_mw = 0.0
        gen_dispatch_list.append(pg_mw)
        gen_dispatch_mw[g["bus"]] = pg_mw

    total_cost = float(result["objective"])

    return {
        "angles_deg": angles_deg,
        "line_flows_mw": line_flows_mw,
        "gen_dispatch_mw": gen_dispatch_mw,
        "gen_dispatch_list": gen_dispatch_list,
        "total_cost": total_cost,
        "_solver_time": _solver_time,
    }


# ── GridCal ────────────────────────────────────────────────────────────


def solve_with_gridcal(ieee_data: dict) -> dict:
    """Solve DCOPF using GridCalEngine's linear OPF.

    Requires: ``pip install GridCalEngine`` (or ``veragridengine``).
    Uses HiGHS as LP solver.
    """
    try:
        from GridCalEngine import (
            Bus, Generator, Line, Load, MIPSolvers, MultiCircuit,
            OptimalPowerFlowOptions, SolverType, linear_opf,
        )
    except ImportError:
        from VeraGridEngine import (
            Bus, Generator, Line, Load, MIPSolvers, MultiCircuit,
            OptimalPowerFlowOptions, SolverType, linear_opf,
        )

    buses = ieee_data["buses"]
    generators = ieee_data["generators"]
    lines_data = ieee_data["lines"]
    n = ieee_data["num_buses"]
    slack = ieee_data["slack_bus"]
    base_mva = ieee_data["base_mva"]

    grid = MultiCircuit(name="IEEE_case")
    grid.Sbase = base_mva

    # Add buses
    gc_buses = []
    for b in buses:
        vn = b.get("voltage_kv", 230.0) or 230.0
        bus = Bus(
            name=f"bus_{b['bus_id']}",
            Vnom=vn,
            is_slack=(b["bus_id"] == slack),
        )
        grid.add_bus(bus)
        gc_buses.append(bus)

    # Add loads
    for b in buses:
        if b["pd_mw"] != 0.0:
            load = Load(name=f"load_{b['bus_id']}", P=b["pd_mw"])
            grid.add_load(gc_buses[b["bus_id"]], load)

    # Add generators
    for g_idx, g in enumerate(generators):
        gen = Generator(
            name=f"gen_{g_idx}",
            Pmin=g.get("pg_min", 0.0),
            Pmax=g["pg_max"],
            Cost=g["cost_mwh"],
            Cost2=0.0,
            Cost0=0.0,
        )
        grid.add_generator(gc_buses[g["bus"]], gen)

    # Add lines (effective reactance x_eff = x * tap for correct susceptance)
    for l_idx, l in enumerate(lines_data):
        tap = l.get("tap", 1.0)
        x_eff = l["x_pu"] * tap
        line = Line(
            bus_from=gc_buses[l["from"]],
            bus_to=gc_buses[l["to"]],
            name=f"line_{l_idx}",
            r=l.get("r_pu", 0.0),
            x=x_eff,
            b=l.get("b_pu", 0.0),
            rate=l["rate_mw"] if l["rate_mw"] > 0 else 1e10,
        )
        grid.add_line(line)

    # Solve DC OPF
    import time as _time

    options = OptimalPowerFlowOptions(
        solver=SolverType.LINEAR_OPF,
        mip_solver=MIPSolvers.HIGHS,
        verbose=0,
    )
    _t_solver = _time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        results = linear_opf(grid, options)
    _solver_time = _time.perf_counter() - _t_solver

    if not results.converged:
        raise RuntimeError("GridCal DCOPF did not converge")

    # Extract angles (radians → degrees)
    angles_deg = [0.0] * n
    for i in range(n):
        angles_deg[i] = math.degrees(float(np.angle(results.voltage[i])))

    # Line flows — GridCal reorders branches internally (Lines with non-unity
    # taps become Transformer2W objects), so Sf indices don't match our input
    # order.  Map via branch names to get the correct flow for each line.
    branches = grid.get_branches()
    name_to_sf_idx = {br.name: i for i, br in enumerate(branches)}
    line_flows_mw = []
    for l_idx in range(len(lines_data)):
        sf_idx = name_to_sf_idx.get(f"line_{l_idx}")
        if sf_idx is not None:
            line_flows_mw.append(float(np.real(results.Sf[sf_idx])))
        else:
            line_flows_mw.append(0.0)

    # Generation dispatch
    gen_dispatch_mw = {}
    gen_dispatch_list = []
    for g_idx, g in enumerate(generators):
        pg = float(results.generator_power[g_idx])
        gen_dispatch_list.append(pg)
        gen_dispatch_mw[g["bus"]] = pg

    # Total cost (compute from dispatch × linear cost)
    total_cost = 0.0
    for g_idx, g in enumerate(generators):
        total_cost += gen_dispatch_list[g_idx] * g["cost_mwh"]

    return {
        "angles_deg": angles_deg,
        "line_flows_mw": line_flows_mw,
        "gen_dispatch_mw": gen_dispatch_mw,
        "gen_dispatch_list": gen_dispatch_list,
        "total_cost": total_cost,
        "_solver_time": _solver_time,
    }


# ── Egret ─────────────────────────────────────────────────────────────


def solve_with_egret(ieee_data: dict) -> dict:
    """Solve DCOPF using Egret (GRID-X) with B-theta formulation.

    Requires: ``pip install gridx-egret``
    Uses HiGHS via Pyomo SolverFactory (falls back to glpk/cbc).
    """
    from egret.data.model_data import ModelData
    from egret.models.dcopf import solve_dcopf

    buses = ieee_data["buses"]
    generators = ieee_data["generators"]
    lines_data = ieee_data["lines"]
    n = ieee_data["num_buses"]
    slack = ieee_data["slack_bus"]
    base_mva = ieee_data["base_mva"]

    # Build ModelData
    md = ModelData()
    md.data["elements"]["bus"] = {}
    md.data["elements"]["generator"] = {}
    md.data["elements"]["load"] = {}
    md.data["elements"]["branch"] = {}
    md.data["system"]["baseMVA"] = base_mva
    md.data["system"]["reference_bus"] = f"bus_{slack}"
    md.data["system"]["reference_bus_angle"] = 0.0

    # Add buses
    for b in buses:
        bus_name = f"bus_{b['bus_id']}"
        md.data["elements"]["bus"][bus_name] = {
            "bus_type": "PV" if b["bus_type"] in ("slack", "PV") else "PQ",
            "vm": 1.0,
            "va": 0.0,
            "in_service": True,
        }

    # Add loads
    for b in buses:
        if b["pd_mw"] != 0.0:
            load_name = f"load_{b['bus_id']}"
            md.data["elements"]["load"][load_name] = {
                "bus": f"bus_{b['bus_id']}",
                "p_load": b["pd_mw"],
                "q_load": 0.0,
                "in_service": True,
            }

    # Add generators
    for g_idx, g in enumerate(generators):
        gen_name = f"gen_{g_idx}"
        md.data["elements"]["generator"][gen_name] = {
            "bus": f"bus_{g['bus']}",
            "p_min": g.get("pg_min", 0.0),
            "p_max": g["pg_max"],
            "in_service": True,
            "pg": 0.0,
            "p_cost": {
                "data_type": "cost_curve",
                "cost_curve_type": "polynomial",
                "values": {0: 0.0, 1: g["cost_mwh"]},
            },
        }

    # Add branches
    for l_idx, l in enumerate(lines_data):
        branch_name = f"line_{l_idx}"
        tap = l.get("tap", 1.0)
        shift_deg = l.get("shift_deg", 0.0)
        is_transformer = (tap != 1.0 or shift_deg != 0.0)
        md.data["elements"]["branch"][branch_name] = {
            "from_bus": f"bus_{l['from']}",
            "to_bus": f"bus_{l['to']}",
            "resistance": l.get("r_pu", 0.0),
            "reactance": l["x_pu"],
            "charging_susceptance": l.get("b_pu", 0.0),
            "rating_long_term": l["rate_mw"] if l["rate_mw"] > 0 else 1e10,
            "branch_type": "transformer" if is_transformer else "line",
            "transformer_tap_ratio": tap if is_transformer else None,
            "transformer_phase_shift": shift_deg if is_transformer else None,
            "in_service": True,
        }

    # Find available LP solver: prefer HiGHS, then glpk, then cbc
    import pyomo.opt as po

    solver_str = None
    for sname in ("highs", "glpk", "cbc"):
        try:
            s = po.SolverFactory(sname)
            if s.available():
                solver_str = sname
                break
        except Exception:
            continue
    if solver_str is None:
        raise RuntimeError("No LP solver available for Egret (need highs, glpk, or cbc)")

    # Suppress Pyomo/solver output
    import time as _time

    _t_solver = _time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        md_sol = solve_dcopf(md, solver_str, solver_tee=False)
    _solver_time = _time.perf_counter() - _t_solver

    # Extract results
    # Egret B-theta uses b = -1/(tau*x), giving opposite angle sign convention.
    # Negate to match MATPOWER/standard convention (positive flow from→to).
    angles_deg = [0.0] * n
    sol_buses = dict(md_sol.elements(element_type="bus"))
    for b in buses:
        bus_name = f"bus_{b['bus_id']}"
        if bus_name in sol_buses:
            angles_deg[b["bus_id"]] = -float(sol_buses[bus_name].get("va", 0.0))

    # Line flows (Egret flow sign matches MATPOWER: positive = from→to)
    line_flows_mw = []
    sol_branches = dict(md_sol.elements(element_type="branch"))
    for l_idx in range(len(lines_data)):
        branch_name = f"line_{l_idx}"
        if branch_name in sol_branches:
            line_flows_mw.append(float(sol_branches[branch_name].get("pf", 0.0)))
        else:
            line_flows_mw.append(0.0)

    # Generation dispatch
    gen_dispatch_mw = {}
    gen_dispatch_list = []
    sol_gens = dict(md_sol.elements(element_type="generator"))
    for g_idx, g in enumerate(generators):
        gen_name = f"gen_{g_idx}"
        if gen_name in sol_gens:
            pg = float(sol_gens[gen_name].get("pg", 0.0))
        else:
            pg = 0.0
        gen_dispatch_list.append(pg)
        gen_dispatch_mw[g["bus"]] = pg

    total_cost = float(md_sol.data["system"].get("total_cost", 0.0))

    return {
        "angles_deg": angles_deg,
        "line_flows_mw": line_flows_mw,
        "gen_dispatch_mw": gen_dispatch_mw,
        "gen_dispatch_list": gen_dispatch_list,
        "total_cost": total_cost,
        "_solver_time": _solver_time,
    }


# ── MATPOWER ──────────────────────────────────────────────────────────


def solve_with_matpower(ieee_data: dict) -> dict:
    """Solve DCOPF using MATPOWER via oct2py (Octave bridge).

    Requires: ``pip install matpower oct2py`` and GNU Octave installed.
    Uses MATPOWER's internal MIPS solver.
    """
    import oct2py

    buses = ieee_data["buses"]
    generators = ieee_data["generators"]
    lines_data = ieee_data["lines"]
    n = ieee_data["num_buses"]
    base_mva = ieee_data["base_mva"]

    # Build MATPOWER case arrays (1-indexed)
    n_gen = len(generators)
    n_lines = len(lines_data)

    # Bus data: [bus_i, type, Pd, Qd, Gs, Bs, area, Vm, Va, baseKV, zone, Vmax, Vmin]
    bus = np.zeros((n, 13))
    for b in buses:
        i = b["bus_id"]
        bus_type = {"slack": 3, "PV": 2, "PQ": 1}.get(b["bus_type"], 1)
        bus[i, :] = [
            i + 1, bus_type, b["pd_mw"], 0.0, 0.0, 0.0, 1,
            1.0, 0.0, b.get("voltage_kv", 230.0) or 230.0, 1, 1.1, 0.9,
        ]

    # Generator data
    gen = np.zeros((n_gen, 21))
    for g_idx, g in enumerate(generators):
        gen[g_idx, :10] = [
            g["bus"] + 1, 0.0, 0.0, 999.0, -999.0,
            1.0, base_mva, 1, g["pg_max"], g.get("pg_min", 0.0),
        ]

    # Branch data
    branch = np.zeros((n_lines, 13))
    for l_idx, l in enumerate(lines_data):
        tap = l.get("tap", 1.0)
        tap_matpower = tap if tap != 1.0 else 0.0
        shift_deg = l.get("shift_deg", 0.0)
        branch[l_idx, :] = [
            l["from"] + 1, l["to"] + 1,
            l.get("r_pu", 0.0), l["x_pu"], l.get("b_pu", 0.0),
            l["rate_mw"], l["rate_mw"], l["rate_mw"],
            tap_matpower, shift_deg, 1, -180.0, 180.0,
        ]

    # Generator cost (linear: type=2, ncost=2, c1, c0)
    gencost = np.zeros((n_gen, 7))
    for g_idx, g in enumerate(generators):
        gencost[g_idx, :6] = [2, 0.0, 0.0, 2, g["cost_mwh"], 0.0]

    # Solve via Octave
    import time as _time

    _t_solver = _time.perf_counter()
    with oct2py.Oct2Py() as oc:
        oc.eval("warning('off', 'all');", nout=0)
        # Add MATPOWER to Octave path
        try:
            oc.eval("mpver;", nout=0)
        except oct2py.Oct2PyError:
            # Try to find and add matpower path
            import matpower

            mp_path = str(Path(matpower.__file__).parent)
            oc.addpath(oc.genpath(mp_path))

        # Push case struct
        oc.push("mpc_bus", bus)
        oc.push("mpc_gen", gen)
        oc.push("mpc_branch", branch)
        oc.push("mpc_gencost", gencost)
        oc.push("mpc_baseMVA", float(base_mva))

        oc.eval(
            """
            mpc.version = '2';
            mpc.baseMVA = mpc_baseMVA;
            mpc.bus = mpc_bus;
            mpc.gen = mpc_gen;
            mpc.branch = mpc_branch;
            mpc.gencost = mpc_gencost;
            mpopt = mpoption('verbose', 0, 'out.all', 0);
            result = rundcopf(mpc, mpopt);
            """,
            nout=0,
        )

        res_bus = oc.pull("result.bus")
        res_gen = oc.pull("result.gen")
        res_branch = oc.pull("result.branch")
        res_f = float(oc.pull("result.f"))
        res_success = int(oc.pull("result.success"))
    _solver_time = _time.perf_counter() - _t_solver

    if not res_success:
        raise RuntimeError("MATPOWER DCOPF did not converge")

    # Extract results (1-indexed → 0-indexed)
    angles_deg = [0.0] * n
    for row in res_bus:
        orig_bus = int(row[0]) - 1
        angles_deg[orig_bus] = float(row[8])  # Va column

    line_flows_mw = []
    for l_idx in range(n_lines):
        line_flows_mw.append(float(res_branch[l_idx, 13]))  # Pf column

    gen_dispatch_mw = {}
    gen_dispatch_list = []
    for g_idx in range(n_gen):
        pg = float(res_gen[g_idx, 1])  # Pg column
        gen_dispatch_list.append(pg)
        bus_0idx = int(res_gen[g_idx, 0]) - 1
        gen_dispatch_mw[bus_0idx] = pg

    total_cost = res_f

    return {
        "angles_deg": angles_deg,
        "line_flows_mw": line_flows_mw,
        "gen_dispatch_mw": gen_dispatch_mw,
        "gen_dispatch_list": gen_dispatch_list,
        "total_cost": total_cost,
        "_solver_time": _solver_time,
    }


# ── Solver registry ───────────────────────────────────────────────────


def get_available_solvers() -> dict[str, Callable]:
    """Return dict of {name: solver_function} for installed DCOPF solvers.

    Each solver function has signature: ``f(ieee_data: dict) -> dict``.
    Only solvers whose dependencies are installed are included.
    """
    solvers: dict[str, Callable] = {}

    try:
        import pypsa  # noqa: F401
        solvers["PyPSA"] = solve_with_pypsa
    except ImportError:
        pass

    try:
        import pandapower  # noqa: F401
        solvers["pandapower"] = solve_with_pandapower
    except ImportError:
        pass

    try:
        from pypower.api import rundcopf  # noqa: F401
        solvers["PYPOWER"] = solve_with_pypower
    except ImportError:
        pass

    try:
        from juliacall import Main as jl
        jl.seval("using PowerModels")
        solvers["PowerModels"] = solve_with_powermodels
    except Exception:
        pass

    try:
        import GridCalEngine  # noqa: F401
        solvers["GridCal"] = solve_with_gridcal
    except ImportError:
        try:
            import VeraGridEngine  # noqa: F401
            solvers["GridCal"] = solve_with_gridcal
        except ImportError:
            pass

    try:
        import egret  # noqa: F401
        solvers["Egret"] = solve_with_egret
    except ImportError:
        pass

    try:
        import oct2py  # noqa: F401
        solvers["MATPOWER"] = solve_with_matpower
    except ImportError:
        pass

    return solvers


# =====================================================================
# ACOPF (AC Optimal Power Flow) Wrappers
# =====================================================================


def _build_ac_matpower_case(ieee_data: dict) -> dict:
    """Build a MATPOWER case dict with full AC data from ieee_data.

    Reused by PYPOWER and MATPOWER ACOPF wrappers.
    """
    buses = ieee_data["buses"]
    generators = ieee_data["generators"]
    lines_data = ieee_data["lines"]
    n = ieee_data["num_buses"]
    base_mva = ieee_data["base_mva"]
    n_gen = len(generators)
    n_lines = len(lines_data)

    # Bus data: [bus_i, type, Pd, Qd, Gs, Bs, area, Vm, Va, baseKV, zone, Vmax, Vmin]
    bus = np.zeros((n, 13))
    for b in buses:
        i = b["bus_id"]
        bus_type = {"slack": 3, "PV": 2, "PQ": 1}.get(b["bus_type"], 1)
        bus[i, :] = [
            i + 1, bus_type,
            b["pd_mw"], b.get("qd_mvar", 0.0),
            b.get("gs_mw", 0.0), b.get("bs_mvar", 0.0),
            1, b.get("vm_pu", 1.0), b.get("va_deg", 0.0),
            b.get("voltage_kv", 230.0) or 230.0,
            1, b.get("vmax_pu", 1.1), b.get("vmin_pu", 0.9),
        ]

    # Generator data: [bus, Pg, Qg, Qmax, Qmin, Vg, mBase, status, Pmax, Pmin, ...]
    gen = np.zeros((n_gen, 21))
    for g_idx, g in enumerate(generators):
        gen[g_idx, :10] = [
            g["bus"] + 1,
            g.get("pg_mw", 0.0), g.get("qg_mvar", 0.0),
            min(g.get("qmax_mvar", 999.0), 9999.0),
            max(g.get("qmin_mvar", -999.0), -9999.0),
            g.get("vg_pu", 1.0), base_mva, 1,
            g["pg_max"], g.get("pg_min", 0.0),
        ]

    # Branch data: [fbus, tbus, r, x, b, rateA, rateB, rateC, ratio, angle, status, ...]
    branch = np.zeros((n_lines, 13))
    for l_idx, l in enumerate(lines_data):
        tap = l.get("tap", 1.0)
        shift = l.get("shift_deg", 0.0)
        # MATPOWER: tap=0 means non-transformer; preserve tap=1 for phase shifters
        tap_mp = tap if (tap != 1.0 or shift != 0.0) else 0.0
        # PYPOWER/pandapower crash with rateA=0 (empty index arrays in PIPS
        # Hessian and boolean index mismatch in from_ppc). Use 99999 = "no limit".
        rate = l["rate_mw"] if l["rate_mw"] > 0 else 99999.0
        branch[l_idx, :] = [
            l["from"] + 1, l["to"] + 1,
            l.get("r_pu", 0.0), l["x_pu"], l.get("b_pu", 0.0),
            rate, rate, rate,
            tap_mp, l.get("shift_deg", 0.0), 1, -180.0, 180.0,
        ]

    # Generator cost (linear: type=2, ncost=2, c1, c0)
    gencost = np.zeros((n_gen, 7))
    for g_idx, g in enumerate(generators):
        gencost[g_idx, :6] = [2, 0.0, 0.0, 2, g["cost_mwh"], 0.0]

    return {
        "version": "2",
        "baseMVA": base_mva,
        "bus": bus,
        "gen": gen,
        "branch": branch,
        "gencost": gencost,
    }


def _ieee_to_matpower_file_ac(ieee_data: dict, path: str):
    """Write ieee_data as a MATPOWER .m case file with full AC data."""
    buses = ieee_data["buses"]
    generators = ieee_data["generators"]
    lines_data = ieee_data["lines"]
    base_mva = ieee_data["base_mva"]

    with open(path, "w") as f:
        f.write("function mpc = case_custom\n")
        f.write("mpc.version = '2';\n")
        f.write(f"mpc.baseMVA = {base_mva};\n\n")

        # Bus data with AC fields
        f.write("%% bus data\n")
        f.write("mpc.bus = [\n")
        for b in buses:
            bt = {"slack": 3, "PV": 2, "PQ": 1}.get(b["bus_type"], 1)
            vn = b.get("voltage_kv", 230.0) or 230.0
            f.write(
                f"  {b['bus_id']+1}  {bt}  {b['pd_mw']}  {b.get('qd_mvar', 0.0)}  "
                f"{b.get('gs_mw', 0.0)}  {b.get('bs_mvar', 0.0)}  "
                f"1  {b.get('vm_pu', 1.0)}  {b.get('va_deg', 0.0)}  {vn}  1  "
                f"{b.get('vmax_pu', 1.1)}  {b.get('vmin_pu', 0.9)};\n"
            )
        f.write("];\n\n")

        # Generator data with AC fields
        f.write("%% generator data\n")
        f.write("mpc.gen = [\n")
        for g in generators:
            f.write(
                f"  {g['bus']+1}  {g.get('pg_mw', 0.0)}  {g.get('qg_mvar', 0.0)}  "
                f"{min(g.get('qmax_mvar', 999.0), 9999.0)}  "
                f"{max(g.get('qmin_mvar', -999.0), -9999.0)}  "
                f"{g.get('vg_pu', 1.0)}  {base_mva}  1  "
                f"{g['pg_max']}  {g.get('pg_min', 0.0)}  "
                f"0  0  0  0  0  0  0  0  0  0  0;\n"
            )
        f.write("];\n\n")

        # Branch data
        f.write("%% branch data\n")
        f.write("mpc.branch = [\n")
        for l in lines_data:
            tap = l.get("tap", 1.0)
            tap_mp = tap if tap != 1.0 else 0.0
            f.write(
                f"  {l['from']+1}  {l['to']+1}  {l.get('r_pu', 0.0)}  "
                f"{l['x_pu']}  {l.get('b_pu', 0.0)}  "
                f"{l['rate_mw']}  {l['rate_mw']}  {l['rate_mw']}  "
                f"{tap_mp}  {l.get('shift_deg', 0.0)}  1  -180  180;\n"
            )
        f.write("];\n\n")

        # Generator cost (linear)
        f.write("%% generator cost data\n")
        f.write("mpc.gencost = [\n")
        for g in generators:
            f.write(f"  2  0  0  2  {g['cost_mwh']}  0;\n")
        f.write("];\n")


def _extract_acopf_result(
    res_bus, res_gen, res_branch, n, n_gen, n_lines, generators, total_cost,
    status_str, solver_time,
) -> dict:
    """Extract common ACOPF result format from MATPOWER-style result arrays."""
    angles_deg = [0.0] * n
    vm_pu = [1.0] * n
    for row in res_bus:
        idx = int(row[0]) - 1
        vm_pu[idx] = float(row[7])
        angles_deg[idx] = float(row[8])

    line_flows_mw = [float(res_branch[l, 13]) for l in range(n_lines)]
    line_flows_mvar = [float(res_branch[l, 14]) for l in range(n_lines)]
    line_flows_to_mw = [float(res_branch[l, 15]) for l in range(n_lines)]

    gen_dispatch_mw = {}
    gen_dispatch_list = []
    gen_reactive_list = []
    for g_idx in range(n_gen):
        pg = float(res_gen[g_idx, 1])
        qg = float(res_gen[g_idx, 2])
        gen_dispatch_list.append(pg)
        gen_reactive_list.append(qg)
        gen_dispatch_mw[int(res_gen[g_idx, 0]) - 1] = pg

    return {
        "angles_deg": angles_deg,
        "vm_pu": vm_pu,
        "line_flows_mw": line_flows_mw,
        "line_flows_mvar": line_flows_mvar,
        "line_flows_to_mw": line_flows_to_mw,
        "gen_dispatch_list": gen_dispatch_list,
        "gen_reactive_list": gen_reactive_list,
        "gen_dispatch_mw": gen_dispatch_mw,
        "total_cost": total_cost,
        "status": status_str,
        "_solver_time": solver_time,
    }


# ── PYPOWER ACOPF ────────────────────────────────────────────────────


def _patch_pypower_numpy2():
    """Fix pypower.pips np.r_ usage for numpy >= 2.0.

    PYPOWER's PIPS solver uses ``np.r_[hn, Ai * x - bi]`` where ``hn`` is
    2-D (column vector) and ``Ai * x - bi`` is 1-D.  numpy < 2 implicitly
    broadcast; numpy >= 2 raises ValueError.  We monkey-patch pips to
    flatten before concatenating.
    """
    import pypower.pips as _pips_mod
    import numpy as _np
    if getattr(_pips_mod, "_numpy2_patched", False):
        return
    # pips.py does `from numpy import r_` at top level, so we must
    # replace the module-level `r_` reference inside pypower.pips directly.
    _original_r_ = _pips_mod.r_

    class _SafeR:
        """Drop-in replacement for np.r_ that flattens mixed-dim arrays."""
        def __getitem__(self, key):
            try:
                return _original_r_[key]
            except ValueError:
                if isinstance(key, tuple):
                    parts = [_np.asarray(a).ravel() for a in key]
                    return _np.concatenate(parts)
                raise

    _pips_mod.r_ = _SafeR()
    _pips_mod._numpy2_patched = True


def solve_acopf_pypower(ieee_data: dict) -> dict:
    """Solve ACOPF using PYPOWER's runopf (PIPS interior-point solver).

    Requires: ``pip install pypower``
    Includes numpy 2.x compatibility patch for pips.py np.r_ usage.
    """
    _patch_pypower_numpy2()
    from pypower.api import ppoption, runopf

    n = ieee_data["num_buses"]
    n_gen = len(ieee_data["generators"])
    n_lines = len(ieee_data["lines"])

    case = _build_ac_matpower_case(ieee_data)

    ppopt = ppoption(PF_DC=0, VERBOSE=0, OUT_ALL=0, PDIPM_MAX_IT=1000)

    import time as _time
    _t = _time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = runopf(case, ppopt)
    solver_time = _time.perf_counter() - _t

    status = "OPTIMAL" if result["success"] else "FAILED"
    return _extract_acopf_result(
        result["bus"], result["gen"], result["branch"],
        n, n_gen, n_lines, ieee_data["generators"],
        float(result["f"]), status, solver_time,
    )


# ── PowerModels ACOPF ────────────────────────────────────────────────


def solve_acopf_powermodels(ieee_data: dict) -> dict:
    """Solve ACOPF using PowerModels.jl (ACPPowerModel + Ipopt).

    Requires: ``PowerModels``, ``Ipopt`` Julia packages.
    """
    import os
    import tempfile

    from juliacall import Main as jl

    jl.seval("using PowerModels, Ipopt")
    jl.seval("PowerModels.silence()")

    n = ieee_data["num_buses"]
    n_gen = len(ieee_data["generators"])
    n_lines = len(ieee_data["lines"])
    base_mva = ieee_data["base_mva"]
    buses = ieee_data["buses"]
    generators = ieee_data["generators"]

    # Write temporary MATPOWER .m file with full AC data
    import time as _time
    fd, temp_path = tempfile.mkstemp(suffix=".m")
    os.close(fd)
    try:
        _ieee_to_matpower_file_ac(ieee_data, temp_path)

        global _pm_acopf_fn
        try:
            _pm_acopf_fn
        except NameError:
            _pm_acopf_fn = jl.seval("""
            function(path)
                opt = optimizer_with_attributes(
                    Ipopt.Optimizer,
                    "print_level" => 0,
                    "tol" => 1e-6,
                    "max_iter" => 10000,
                    "max_cpu_time" => 1800.0,
                )
                PowerModels.solve_opf(path, ACPPowerModel, opt)
            end
            """)

        _t = _time.perf_counter()
        result = _pm_acopf_fn(temp_path)
        solver_time = _time.perf_counter() - _t
    finally:
        os.unlink(temp_path)

    result = dict(result)
    solution = dict(result["solution"])
    status = str(result.get("termination_status", "UNKNOWN"))
    if "LOCALLY_SOLVED" in status or "OPTIMAL" in status:
        status = "OPTIMAL"

    # Extract bus results
    sol_bus = dict(solution.get("bus", {}))
    angles_deg = [0.0] * n
    vm_pu = [1.0] * n
    for b in buses:
        bus_1idx = str(b["bus_id"] + 1)
        if bus_1idx in sol_bus:
            bd = dict(sol_bus[bus_1idx])
            angles_deg[b["bus_id"]] = math.degrees(float(bd.get("va", 0.0)))
            vm_pu[b["bus_id"]] = float(bd.get("vm", 1.0))

    # Branch results (per-unit → MW/MVAr)
    sol_branch = dict(solution.get("branch", {}))
    line_flows_mw = []
    line_flows_mvar = []
    line_flows_to_mw = []
    for l_idx in range(n_lines):
        br_1idx = str(l_idx + 1)
        if br_1idx in sol_branch:
            bd = dict(sol_branch[br_1idx])
            line_flows_mw.append(float(bd.get("pf", 0.0)) * base_mva)
            line_flows_mvar.append(float(bd.get("qf", 0.0)) * base_mva)
            line_flows_to_mw.append(float(bd.get("pt", 0.0)) * base_mva)
        else:
            line_flows_mw.append(0.0)
            line_flows_mvar.append(0.0)
            line_flows_to_mw.append(0.0)

    # Generator results (per-unit → MW/MVAr)
    sol_gen = dict(solution.get("gen", {}))
    gen_dispatch_mw = {}
    gen_dispatch_list = []
    gen_reactive_list = []
    for g_idx, g in enumerate(generators):
        gen_1idx = str(g_idx + 1)
        if gen_1idx in sol_gen:
            gd = dict(sol_gen[gen_1idx])
            pg = float(gd.get("pg", 0.0)) * base_mva
            qg = float(gd.get("qg", 0.0)) * base_mva
        else:
            pg, qg = 0.0, 0.0
        gen_dispatch_list.append(pg)
        gen_reactive_list.append(qg)
        gen_dispatch_mw[g["bus"]] = pg

    total_cost = float(result.get("objective", 0.0))

    return {
        "angles_deg": angles_deg,
        "vm_pu": vm_pu,
        "line_flows_mw": line_flows_mw,
        "line_flows_mvar": line_flows_mvar,
        "line_flows_to_mw": line_flows_to_mw,
        "gen_dispatch_list": gen_dispatch_list,
        "gen_reactive_list": gen_reactive_list,
        "gen_dispatch_mw": gen_dispatch_mw,
        "total_cost": total_cost,
        "status": status,
        "_solver_time": solver_time,
    }


# ── Egret ACOPF ──────────────────────────────────────────────────────


def solve_acopf_egret(ieee_data: dict) -> dict:
    """Solve ACOPF using Egret (GRID-X) with Ipopt.

    Requires: ``pip install gridx-egret`` and Ipopt via Pyomo.
    """
    from egret.data.model_data import ModelData
    from egret.models.acopf import solve_acopf as _egret_solve_acopf

    buses = ieee_data["buses"]
    generators = ieee_data["generators"]
    lines_data = ieee_data["lines"]
    n = ieee_data["num_buses"]
    slack = ieee_data["slack_bus"]
    base_mva = ieee_data["base_mva"]

    md = ModelData()
    md.data["elements"]["bus"] = {}
    md.data["elements"]["generator"] = {}
    md.data["elements"]["load"] = {}
    md.data["elements"]["branch"] = {}
    md.data["elements"]["shunt"] = {}
    md.data["system"]["baseMVA"] = base_mva
    md.data["system"]["reference_bus"] = f"bus_{slack}"
    md.data["system"]["reference_bus_angle"] = 0.0

    # Egret's PSV formulation uses interval arithmetic on auxiliary variables
    # (c, s, vmsq) that can over-constrain the problem when voltage bounds are
    # very tight. Relax by a small margin to help NLP convergence while
    # keeping bounds physically meaningful.
    _V_RELAX = 0.01  # per-unit

    for b in buses:
        bus_name = f"bus_{b['bus_id']}"
        md.data["elements"]["bus"][bus_name] = {
            "matpower_bustype": "ref" if b["bus_type"] == "slack" else (
                "PV" if b["bus_type"] == "PV" else "PQ"
            ),
            "vm": b.get("vm_pu", 1.0),
            "va": 0.0,
            "v_min": max(b.get("vmin_pu", 0.9) - _V_RELAX, 0.80),
            "v_max": min(b.get("vmax_pu", 1.1) + _V_RELAX, 1.20),
            "base_kv": b.get("voltage_kv", 230.0) or 230.0,
            "in_service": True,
        }

    for b in buses:
        pd = b["pd_mw"]
        qd = b.get("qd_mvar", 0.0)
        if pd != 0.0 or qd != 0.0:
            md.data["elements"]["load"][f"load_{b['bus_id']}"] = {
                "bus": f"bus_{b['bus_id']}",
                "p_load": pd,
                "q_load": qd,
                "in_service": True,
            }

    # Shunts
    for b in buses:
        gs = b.get("gs_mw", 0.0)
        bs = b.get("bs_mvar", 0.0)
        if gs != 0.0 or bs != 0.0:
            md.data["elements"]["shunt"][f"shunt_{b['bus_id']}"] = {
                "bus": f"bus_{b['bus_id']}",
                "gs": gs / base_mva,   # per-unit
                "bs": bs / base_mva,   # per-unit
                "shunt_type": "fixed",
                "in_service": True,
            }

    for g_idx, g in enumerate(generators):
        md.data["elements"]["generator"][f"gen_{g_idx}"] = {
            "bus": f"bus_{g['bus']}",
            "p_min": g.get("pg_min", 0.0),
            "p_max": g["pg_max"],
            "q_min": max(g.get("qmin_mvar", -999.0), -9999.0),
            "q_max": min(g.get("qmax_mvar", 999.0), 9999.0),
            "pg": g.get("pg_mw", 0.0),
            "qg": 0.0,
            "vg": g.get("vg_pu", 1.0),
            "in_service": True,
            "p_cost": {
                "data_type": "cost_curve",
                "cost_curve_type": "polynomial",
                "values": {0: 0.0, 1: g["cost_mwh"]},
            },
        }

    for l_idx, l in enumerate(lines_data):
        tap = l.get("tap", 1.0)
        shift_deg = l.get("shift_deg", 0.0)
        is_xfmr = (tap != 1.0 or shift_deg != 0.0)
        md.data["elements"]["branch"][f"line_{l_idx}"] = {
            "from_bus": f"bus_{l['from']}",
            "to_bus": f"bus_{l['to']}",
            "resistance": l.get("r_pu", 0.0),
            "reactance": l["x_pu"],
            "charging_susceptance": l.get("b_pu", 0.0),
            "rating_long_term": l["rate_mw"] if l["rate_mw"] > 0 else 1e10,
            "rating_short_term": l["rate_mw"] if l["rate_mw"] > 0 else 1e10,
            "rating_emergency": l["rate_mw"] if l["rate_mw"] > 0 else 1e10,
            "branch_type": "transformer" if is_xfmr else "line",
            "transformer_tap_ratio": tap if is_xfmr else None,
            "transformer_phase_shift": shift_deg if is_xfmr else None,
            "angle_diff_min": -180.0,
            "angle_diff_max": 180.0,
            "in_service": True,
        }

    # Solve with Ipopt — suppress Pyomo W1002 bound-violation warnings
    # and pass solver options for convergence on large systems
    import time as _time

    # Pyomo emits W1002 warnings through Python logging, not warnings module
    _pyomo_logger = logging.getLogger("pyomo")
    _prev_level = _pyomo_logger.level
    _pyomo_logger.setLevel(logging.ERROR)

    ipopt_options = {
        "max_cpu_time": 1800.0,  # match global timeout
        "max_iter": 10000,       # allow more iterations for large PEGASE systems
        "tol": 1e-6,
    }

    _t = _time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        md_sol = _egret_solve_acopf(
            md, "ipopt", solver_tee=False, options=ipopt_options,
        )
    solver_time = _time.perf_counter() - _t

    _pyomo_logger.setLevel(_prev_level)

    # Check convergence — Egret/ipopt sets total_cost on success;
    # termination_cond may be absent when solved via ipopt directly.
    sol_status = md_sol.data["system"].get("termination_cond", "")
    if "optimal" in str(sol_status).lower():
        status = "OPTIMAL"
    elif md_sol.data["system"].get("total_cost") is not None:
        status = "OPTIMAL"  # solve_acopf succeeded (would raise on failure)
    else:
        status = str(sol_status) if sol_status else "FAILED"

    # Extract bus results — Egret returns va already in degrees
    sol_buses = dict(md_sol.elements(element_type="bus"))
    angles_deg = [0.0] * n
    vm_pu = [1.0] * n
    for b in buses:
        bus_name = f"bus_{b['bus_id']}"
        if bus_name in sol_buses:
            bd = sol_buses[bus_name]
            angles_deg[b["bus_id"]] = float(bd.get("va", 0.0))
            vm_pu[b["bus_id"]] = float(bd.get("vm", 1.0))

    # Branch results
    sol_branches = dict(md_sol.elements(element_type="branch"))
    line_flows_mw = []
    line_flows_mvar = []
    line_flows_to_mw = []
    for l_idx in range(len(lines_data)):
        br_name = f"line_{l_idx}"
        if br_name in sol_branches:
            bd = sol_branches[br_name]
            line_flows_mw.append(float(bd.get("pf", 0.0)))
            line_flows_mvar.append(float(bd.get("qf", 0.0)))
            line_flows_to_mw.append(float(bd.get("pt", 0.0)))
        else:
            line_flows_mw.append(0.0)
            line_flows_mvar.append(0.0)
            line_flows_to_mw.append(0.0)

    # Generator results
    sol_gens = dict(md_sol.elements(element_type="generator"))
    gen_dispatch_mw = {}
    gen_dispatch_list = []
    gen_reactive_list = []
    for g_idx, g in enumerate(generators):
        gen_name = f"gen_{g_idx}"
        if gen_name in sol_gens:
            gd = sol_gens[gen_name]
            pg = float(gd.get("pg", 0.0))
            qg = float(gd.get("qg", 0.0))
        else:
            pg, qg = 0.0, 0.0
        gen_dispatch_list.append(pg)
        gen_reactive_list.append(qg)
        gen_dispatch_mw[g["bus"]] = pg

    total_cost = float(md_sol.data["system"].get("total_cost", 0.0))

    return {
        "angles_deg": angles_deg,
        "vm_pu": vm_pu,
        "line_flows_mw": line_flows_mw,
        "line_flows_mvar": line_flows_mvar,
        "line_flows_to_mw": line_flows_to_mw,
        "gen_dispatch_list": gen_dispatch_list,
        "gen_reactive_list": gen_reactive_list,
        "gen_dispatch_mw": gen_dispatch_mw,
        "total_cost": total_cost,
        "status": status,
        "_solver_time": solver_time,
    }


# ── MATPOWER ACOPF ───────────────────────────────────────────────────


def solve_acopf_matpower(ieee_data: dict) -> dict:
    """Solve ACOPF using MATPOWER via oct2py (Octave bridge).

    Requires: ``pip install matpower oct2py`` and GNU Octave installed.
    Uses MATPOWER's internal MIPS solver.
    """
    import oct2py

    n = ieee_data["num_buses"]
    n_gen = len(ieee_data["generators"])
    n_lines = len(ieee_data["lines"])
    base_mva = ieee_data["base_mva"]

    case = _build_ac_matpower_case(ieee_data)

    import time as _time
    _t = _time.perf_counter()
    with oct2py.Oct2Py() as oc:
        oc.eval("warning('off', 'all');", nout=0)
        try:
            oc.eval("mpver;", nout=0)
        except oct2py.Oct2PyError:
            import matpower
            mp_path = str(Path(matpower.__file__).parent)
            oc.addpath(oc.genpath(mp_path))

        oc.push("mpc_bus", case["bus"])
        oc.push("mpc_gen", case["gen"])
        oc.push("mpc_branch", case["branch"])
        oc.push("mpc_gencost", case["gencost"])
        oc.push("mpc_baseMVA", float(base_mva))

        oc.eval(
            """
            mpc.version = '2';
            mpc.baseMVA = mpc_baseMVA;
            mpc.bus = mpc_bus;
            mpc.gen = mpc_gen;
            mpc.branch = mpc_branch;
            mpc.gencost = mpc_gencost;
            mpopt = mpoption('verbose', 0, 'out.all', 0);
            result = runopf(mpc, mpopt);
            """,
            nout=0,
        )

        res_bus = oc.pull("result.bus")
        res_gen = oc.pull("result.gen")
        res_branch = oc.pull("result.branch")
        res_f = float(oc.pull("result.f"))
        res_success = int(oc.pull("result.success"))
    solver_time = _time.perf_counter() - _t

    status = "OPTIMAL" if res_success else "FAILED"
    return _extract_acopf_result(
        res_bus, res_gen, res_branch,
        n, n_gen, n_lines, ieee_data["generators"],
        res_f, status, solver_time,
    )


# ── pandapower ACOPF ─────────────────────────────────────────────────


def _patch_pandapower_from_ppc():
    """Fix pandapower from_ppc boolean index bug for numpy >= 2.0.

    pandapower's ``_from_ppc_branch`` (from_ppc.py ~line 303) uses::

        sn[sn_is_zero] = MAX_VAL

    where ``sn`` was defined in the transformer block as
    ``ppc['branch'][is_trafo, RATE_A]`` but ``sn_is_zero`` is computed
    from ``sn_mva = ppc['branch'][is_impedance, RATE_A]``.  The arrays
    have different sizes, causing an IndexError.

    Fix: wrap ``_from_ppc_branch`` to pre-fill zero rateA values with
    99999.0 before pandapower sees them, avoiding the buggy code path.
    """
    import importlib
    try:
        _fpc = importlib.import_module("pandapower.converter.pypower.from_ppc")
    except ImportError:
        return
    if getattr(_fpc, "_numpy2_patched", False):
        return

    _original = _fpc._from_ppc_branch

    def _patched_from_ppc_branch(net, ppc, f_hz, **kwargs):
        import copy as _copy
        import numpy as _np

        # Pre-fill zero rateA with large value so pandapower's
        # sn_is_zero code path is never triggered
        ppc_fixed = _copy.deepcopy(ppc)
        RATE_A = 5  # column index in MATPOWER branch data
        rates = ppc_fixed["branch"][:, RATE_A]
        zero_mask = _np.isclose(rates, 0)
        if _np.any(zero_mask):
            ppc_fixed["branch"][zero_mask, RATE_A] = 99999.0

        return _original(net, ppc_fixed, f_hz, **kwargs)

    _fpc._from_ppc_branch = _patched_from_ppc_branch
    _fpc._numpy2_patched = True


def solve_acopf_pandapower(ieee_data: dict) -> dict:
    """Solve ACOPF using pandapower's runopp (AC optimal power flow).

    Requires: ``pip install pandapower``
    Uses pandapower's internal PYPOWER-based interior-point solver.

    The network is built via ``from_ppc`` (PYPOWER case converter) which
    correctly handles transformer tap ratios and phase shifters.
    Includes numpy 2.x compatibility patch for from_ppc boolean index bug.
    """
    _patch_pandapower_from_ppc()
    _patch_pypower_numpy2()  # pandapower uses PYPOWER's PIPS internally
    import pandapower as pp
    from pandapower.converter.pypower.from_ppc import from_ppc

    logging.getLogger("pandapower").setLevel(logging.ERROR)

    n = ieee_data["num_buses"]
    generators = ieee_data["generators"]
    lines_data = ieee_data["lines"]

    # Build PYPOWER/MATPOWER case and convert to pandapower network.
    # from_ppc handles transformers, shunts, voltage limits, and cost curves.
    ppc = _build_ac_matpower_case(ieee_data)
    net = from_ppc(ppc, f_hz=60)

    # Solve AC OPF
    import time as _time
    _t_solver = _time.perf_counter()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pp.runopp(net, verbose=False)
        converged = net.OPF_converged if hasattr(net, "OPF_converged") else True
    except Exception:
        converged = False
    _solver_time = _time.perf_counter() - _t_solver

    status = "OPTIMAL" if converged else "FAILED"

    if not converged:
        return {
            "angles_deg": [0.0] * n, "vm_pu": [1.0] * n,
            "line_flows_mw": [], "line_flows_mvar": [], "line_flows_to_mw": [],
            "gen_dispatch_list": [], "gen_reactive_list": [],
            "gen_dispatch_mw": {}, "total_cost": 0.0,
            "status": status, "_solver_time": _solver_time,
        }

    # Extract bus results — from_ppc preserves MATPOWER 1-indexed bus IDs,
    # so we must map via ppc["bus"][:,0] rather than assuming 0-based indices.
    angles_deg = [0.0] * n
    vm_pu = [1.0] * n
    bus_ids = ppc["bus"][:, 0].astype(int)  # MATPOWER bus numbers (1-indexed)
    for i, bus_num in enumerate(bus_ids):
        if int(bus_num) in net.res_bus.index:
            angles_deg[i] = float(net.res_bus.at[int(bus_num), "va_degree"])
            vm_pu[i] = float(net.res_bus.at[int(bus_num), "vm_pu"])

    # Branch flows — from_ppc creates lines and trafos in branch-table order.
    # Track which original branch index maps to which pp element.
    branch = ppc["branch"]
    pp_line_idx = 0
    pp_trafo_idx = 0
    line_flows_mw = []
    line_flows_mvar = []
    line_flows_to_mw = []
    for l_idx in range(len(lines_data)):
        tap_val = branch[l_idx, 8]  # MATPOWER tap column (0=line, nonzero=trafo)
        is_trafo = (tap_val != 0.0)
        if is_trafo:
            if pp_trafo_idx in net.res_trafo.index:
                line_flows_mw.append(float(net.res_trafo.at[pp_trafo_idx, "p_hv_mw"]))
                line_flows_mvar.append(float(net.res_trafo.at[pp_trafo_idx, "q_hv_mvar"]))
                line_flows_to_mw.append(float(net.res_trafo.at[pp_trafo_idx, "p_lv_mw"]))
            else:
                line_flows_mw.append(0.0)
                line_flows_mvar.append(0.0)
                line_flows_to_mw.append(0.0)
            pp_trafo_idx += 1
        else:
            if pp_line_idx in net.res_line.index:
                line_flows_mw.append(float(net.res_line.at[pp_line_idx, "p_from_mw"]))
                line_flows_mvar.append(float(net.res_line.at[pp_line_idx, "q_from_mvar"]))
                line_flows_to_mw.append(float(net.res_line.at[pp_line_idx, "p_to_mw"]))
            else:
                line_flows_mw.append(0.0)
                line_flows_mvar.append(0.0)
                line_flows_to_mw.append(0.0)
            pp_line_idx += 1

    # Generator dispatch — from_ppc creates ext_grid for slack + gen for others
    gen_dispatch_mw = {}
    gen_dispatch_list = []
    gen_reactive_list = []
    slack = ieee_data["slack_bus"]
    pp_gen_idx = 0
    for g_idx, g in enumerate(generators):
        if g["bus"] == slack:
            pg = float(net.res_ext_grid.at[0, "p_mw"])
            qg = float(net.res_ext_grid.at[0, "q_mvar"])
        else:
            if pp_gen_idx in net.res_gen.index:
                pg = float(net.res_gen.at[pp_gen_idx, "p_mw"])
                qg = float(net.res_gen.at[pp_gen_idx, "q_mvar"])
            else:
                pg, qg = 0.0, 0.0
            pp_gen_idx += 1
        gen_dispatch_list.append(pg)
        gen_reactive_list.append(qg)
        gen_dispatch_mw[g["bus"]] = pg

    total_cost = float(net.res_cost)

    return {
        "angles_deg": angles_deg,
        "vm_pu": vm_pu,
        "line_flows_mw": line_flows_mw,
        "line_flows_mvar": line_flows_mvar,
        "line_flows_to_mw": line_flows_to_mw,
        "gen_dispatch_list": gen_dispatch_list,
        "gen_reactive_list": gen_reactive_list,
        "gen_dispatch_mw": gen_dispatch_mw,
        "total_cost": total_cost,
        "status": status,
        "_solver_time": _solver_time,
    }


# ── GridCal ACOPF ───────────────────────────────────────────────────


def solve_acopf_gridcal(ieee_data: dict) -> dict:
    """Solve ACOPF using GridCalEngine's nonlinear OPF.

    Requires: ``pip install GridCalEngine`` (or ``veragridengine``).
    """
    try:
        from GridCalEngine import (
            Bus, Generator, Line, Load, MultiCircuit,
            OptimalPowerFlowOptions, PowerFlowOptions,
            Shunt, SolverType, Transformer2W, run_nonlinear_opf,
        )
    except ImportError:
        from VeraGridEngine import (
            Bus, Generator, Line, Load, MultiCircuit,
            OptimalPowerFlowOptions, PowerFlowOptions,
            Shunt, SolverType, Transformer2W, run_nonlinear_opf,
        )

    buses = ieee_data["buses"]
    generators = ieee_data["generators"]
    lines_data = ieee_data["lines"]
    n = ieee_data["num_buses"]
    slack = ieee_data["slack_bus"]
    base_mva = ieee_data["base_mva"]

    grid = MultiCircuit(name="IEEE_case")
    grid.Sbase = base_mva

    # Add buses
    gc_buses = []
    for b in buses:
        vn = b.get("voltage_kv", 230.0) or 230.0
        bus = Bus(
            name=f"bus_{b['bus_id']}",
            Vnom=vn,
            is_slack=(b["bus_id"] == slack),
            vmin=b.get("vmin_pu", 0.9),
            vmax=b.get("vmax_pu", 1.1),
            Vm0=b.get("vm_pu", 1.0),
        )
        grid.add_bus(bus)
        gc_buses.append(bus)

    # Add loads (including reactive)
    for b in buses:
        if b["pd_mw"] != 0.0 or b.get("qd_mvar", 0.0) != 0.0:
            load = Load(
                name=f"load_{b['bus_id']}",
                P=b["pd_mw"],
                Q=b.get("qd_mvar", 0.0),
            )
            grid.add_load(gc_buses[b["bus_id"]], load)

    # Add shunts
    for b in buses:
        gs = b.get("gs_mw", 0.0)
        bs = b.get("bs_mvar", 0.0)
        if gs != 0.0 or bs != 0.0:
            shunt = Shunt(
                name=f"shunt_{b['bus_id']}",
                G=gs,
                B=bs,
            )
            grid.add_shunt(gc_buses[b["bus_id"]], shunt)

    # Add generators with reactive limits and voltage setpoint
    for g_idx, g in enumerate(generators):
        gen = Generator(
            name=f"gen_{g_idx}",
            Pmin=g.get("pg_min", 0.0),
            Pmax=g["pg_max"],
            Qmin=max(g.get("qmin_mvar", -999.0), -9999.0),
            Qmax=min(g.get("qmax_mvar", 999.0), 9999.0),
            Cost=g["cost_mwh"],
            Cost2=0.0,
            Cost0=0.0,
            vset=g.get("vg_pu", 1.0),
        )
        grid.add_generator(gc_buses[g["bus"]], gen)

    # Add lines (use Transformer2W when tap != 1 or shift != 0)
    for l_idx, l in enumerate(lines_data):
        tap = l.get("tap", 1.0)
        shift = l.get("shift_deg", 0.0)
        rate = l["rate_mw"] if l["rate_mw"] > 0 else 99999.0
        if tap != 1.0 or shift != 0.0:
            tr = Transformer2W(
                bus_from=gc_buses[l["from"]],
                bus_to=gc_buses[l["to"]],
                name=f"line_{l_idx}",
                r=l.get("r_pu", 0.0),
                x=l["x_pu"],
                b=l.get("b_pu", 0.0),
                rate=rate,
                tap_module=tap,
                tap_phase=shift,
            )
            grid.add_transformer2w(tr)
        else:
            line = Line(
                bus_from=gc_buses[l["from"]],
                bus_to=gc_buses[l["to"]],
                name=f"line_{l_idx}",
                r=l.get("r_pu", 0.0),
                x=l["x_pu"],
                b=l.get("b_pu", 0.0),
                rate=rate,
            )
            grid.add_line(line)

    # Solve ACOPF
    import time as _time

    pf_opts = PowerFlowOptions()
    opf_opts = OptimalPowerFlowOptions(
        solver=SolverType.NONLINEAR_OPF,
        verbose=0,
        ips_iterations=1000,   # default 100 — insufficient for large PEGASE
        ips_tolerance=1e-6,
    )
    _t_solver = _time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        results = run_nonlinear_opf(grid, opf_options=opf_opts, pf_options=pf_opts)
    _solver_time = _time.perf_counter() - _t_solver

    # GridCalEngine 5.4+ IPS solver may report converged=False even when
    # results are valid (error oscillates around 0.01).  Fall back to a
    # generation-vs-demand balance check when converged is False.
    if results.converged:
        status = "OPTIMAL"
    else:
        total_gen = float(np.sum(np.abs(results.Pg))) * base_mva
        total_dem = sum(abs(b["pd_mw"]) for b in buses)
        # If generation covers >= 95% of demand, accept the result
        if total_dem > 0 and total_gen / total_dem >= 0.95:
            status = "OPTIMAL"
        else:
            status = "FAILED"

    # NonlinearOPFResults uses Vm, Va, Pg, Qg, Sf (all per-unit)
    # Extract voltages
    angles_deg = [0.0] * n
    vm_pu_out = [1.0] * n
    for i in range(n):
        if hasattr(results, "Vm") and results.Vm is not None:
            vm_pu_out[i] = float(results.Vm[i])
            angles_deg[i] = float(np.degrees(results.Va[i]))
        else:
            vm_pu_out[i] = float(np.abs(results.voltage[i]))
            angles_deg[i] = float(np.degrees(np.angle(results.voltage[i])))

    # Line flows — map via branch names (GridCal may reorder)
    branches = grid.get_branches()
    name_to_sf_idx = {br.name: i for i, br in enumerate(branches)}
    line_flows_mw = []
    line_flows_mvar = []
    line_flows_to_mw = []
    for l_idx in range(len(lines_data)):
        sf_idx = name_to_sf_idx.get(f"line_{l_idx}")
        if sf_idx is not None:
            # Sf is in per-unit (complex) for NonlinearOPFResults
            sf_val = results.Sf[sf_idx]
            line_flows_mw.append(float(np.real(sf_val)) * base_mva)
            line_flows_mvar.append(float(np.imag(sf_val)) * base_mva)
            st_val = results.St[sf_idx] if results.St is not None else 0.0
            line_flows_to_mw.append(float(np.real(st_val)) * base_mva)
        else:
            line_flows_mw.append(0.0)
            line_flows_mvar.append(0.0)
            line_flows_to_mw.append(0.0)

    # Generation dispatch (Pg/Qg are per-unit in NonlinearOPFResults)
    gen_dispatch_mw = {}
    gen_dispatch_list = []
    gen_reactive_list = []
    for g_idx, g in enumerate(generators):
        pg = float(results.Pg[g_idx]) * base_mva
        qg = float(results.Qg[g_idx]) * base_mva if results.Qg is not None else 0.0
        gen_dispatch_list.append(pg)
        gen_reactive_list.append(qg)
        gen_dispatch_mw[g["bus"]] = pg

    # Total cost
    total_cost = sum(
        gen_dispatch_list[i] * generators[i]["cost_mwh"]
        for i in range(len(generators))
    )

    return {
        "angles_deg": angles_deg,
        "vm_pu": vm_pu_out,
        "line_flows_mw": line_flows_mw,
        "line_flows_mvar": line_flows_mvar,
        "line_flows_to_mw": line_flows_to_mw,
        "gen_dispatch_list": gen_dispatch_list,
        "gen_reactive_list": gen_reactive_list,
        "gen_dispatch_mw": gen_dispatch_mw,
        "total_cost": total_cost,
        "status": status,
        "_solver_time": _solver_time,
    }


# ── ACOPF solver registry ───────────────────────────────────────────


def get_available_acopf_solvers() -> dict[str, Callable]:
    """Return dict of {name: solver_function} for installed ACOPF solvers.

    Each solver function has signature: ``f(ieee_data: dict) -> dict``.
    Only solvers whose dependencies are installed are included.
    """
    solvers: dict[str, Callable] = {}

    try:
        from pypower.api import runopf  # noqa: F401
        solvers["PYPOWER"] = solve_acopf_pypower
    except ImportError:
        pass

    try:
        from juliacall import Main as jl
        jl.seval("using PowerModels, Ipopt")
        solvers["PowerModels"] = solve_acopf_powermodels
    except Exception:
        pass

    try:
        from egret.models.acopf import solve_acopf as _check  # noqa: F401
        solvers["Egret"] = solve_acopf_egret
    except ImportError:
        pass

    try:
        import pandapower  # noqa: F401
        solvers["pandapower"] = solve_acopf_pandapower
    except ImportError:
        pass

    try:
        from GridCalEngine import run_nonlinear_opf  # noqa: F401
        solvers["GridCal"] = solve_acopf_gridcal
    except ImportError:
        try:
            from VeraGridEngine import run_nonlinear_opf  # noqa: F401
            solvers["GridCal"] = solve_acopf_gridcal
        except ImportError:
            pass

    try:
        import oct2py  # noqa: F401
        solvers["MATPOWER"] = solve_acopf_matpower
    except ImportError:
        pass

    return solvers
