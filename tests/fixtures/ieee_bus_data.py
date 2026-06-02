"""IEEE standard bus system data for DCOPF validation.

All systems loaded from the ``matpower`` Python package (``pip install matpower``).
Data follows standard IEEE publications with reactance in per-unit on 100 MVA base.

References:
- IEEE 9-bus: MATPOWER case9.m (WSCC 3-machine system)
- IEEE 14-bus: MATPOWER case14.m (IEEE Common Data Format)
- IEEE 30-bus: MATPOWER case_ieee30.m (IEEE Common Data Format)
- IEEE 57-bus: MATPOWER case57.m (AEP system, I. Dabbagchi)
- IEEE 118-bus: MATPOWER case118.m (AEP system, 1962)
- IEEE 300-bus: MATPOWER case300.m (IEEE CDF)

DC power flow reference solutions computed analytically:
  θ = B⁻¹ · P_inj  (reduced system, slack bus removed)
  P_flow(i→j) = (θ_i - θ_j) / x_ij
"""

from __future__ import annotations

import os
import re

import numpy as np


def ieee_9bus() -> dict:
    """IEEE 9-bus system (WSCC 3-machine, 9-bus).

    Loaded from MATPOWER ``case9.m``.  Requires ``pip install matpower``.
    9 buses, 9 lines, 3 generators.  Total load: 315 MW.  Slack bus: 0.
    """
    return _parse_matpower_case("case9")


def ieee_14bus() -> dict:
    """IEEE 14-bus system.

    Loaded from MATPOWER ``case14.m``.  Requires ``pip install matpower``.
    14 buses, 20 lines, 5 generators.  Total load: 259 MW.  Slack bus: 0.
    """
    return _parse_matpower_case("case14")


def ieee_30bus() -> dict:
    """IEEE 30-bus system (IEEE Common Data Format variant).

    Loaded from MATPOWER ``case_ieee30.m``.  Requires ``pip install matpower``.
    30 buses, 41 lines, 6 generators.  Total load: ~283 MW.  Slack bus: 0.

    Note: uses ``case_ieee30`` (not ``case30``) which matches the original
    IEEE CDF generator placement at buses 1, 2, 5, 8, 11, 13.
    """
    return _parse_matpower_case("case_ieee30")


def ieee_57bus() -> dict:
    """IEEE 57-bus system (AEP system, I. Dabbagchi).

    Loaded from MATPOWER ``case57.m``.  Requires ``pip install matpower``.
    57 buses, 80 lines, 7 generators.  Total load: ~1251 MW.  Slack bus: 0.
    """
    d = _parse_matpower_case("case57")
    # 57-bus needs wider angle limit: long electrical paths with high-reactance
    # lines (e.g. x=0.77 pu on line 20-19) cause large angle differences.
    d["max_angle_diff_deg"] = 90.0
    return d


def _parse_matpower_matrix(content: str, matrix_name: str) -> list[list[float]]:
    """Parse a named matrix from MATPOWER .m file content."""
    pattern = rf"{re.escape(matrix_name)}\s*=\s*\[(.*?)\];"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return []
    rows = []
    for line in match.group(1).strip().split("\n"):
        line = line.strip().rstrip(";").strip()
        if not line or line.startswith("%") or line.startswith("//"):
            continue
        try:
            rows.append([float(v) for v in line.split()])
        except ValueError:
            continue
    return rows


def _parse_matpower_case(case_name: str) -> dict:
    """Parse a MATPOWER case file and return standardised IEEE dict.

    Requires the ``matpower`` Python package (``pip install matpower``).
    Handles non-sequential bus numbering (remapped to 0-indexed) and
    assigns generous line ratings (9900 MW) when missing from source data.
    Generator costs are linearised from the quadratic ``gencost`` data
    as ``c1 + c2 * pg_max`` (average marginal cost over operating range).

    Parameters
    ----------
    case_name : str
        MATPOWER case file stem, e.g. ``"case118"`` or ``"case300"``.
    """
    import matpower  # optional dependency

    data_dir = os.path.join(os.path.dirname(matpower.__file__), "data")
    filepath = os.path.join(data_dir, f"{case_name}.m")
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"MATPOWER case not found: {filepath}")

    with open(filepath) as fh:
        content = fh.read()

    raw_bus = _parse_matpower_matrix(content, "mpc.bus")
    raw_branch = _parse_matpower_matrix(content, "mpc.branch")
    raw_gen = _parse_matpower_matrix(content, "mpc.gen")
    raw_gencost = _parse_matpower_matrix(content, "mpc.gencost")

    # --- Bus remapping (original 1-indexed, possibly non-sequential → 0-indexed) ---
    orig_ids = sorted(int(r[0]) for r in raw_bus)
    bus_map = {orig: idx for idx, orig in enumerate(orig_ids)}
    n = len(orig_ids)
    base_mva = 100.0

    # MATPOWER bus types: 1=PQ, 2=PV, 3=slack
    type_map = {1: "PQ", 2: "PV", 3: "slack"}
    slack_orig = next(int(r[0]) for r in raw_bus if int(r[1]) == 3)
    slack_bus = bus_map[slack_orig]

    buses = []
    for r in raw_bus:
        orig_id = int(r[0])
        buses.append({
            "bus_id": bus_map[orig_id],
            "bus_type": type_map.get(int(r[1]), "PQ"),
            "voltage_kv": r[9] if len(r) > 9 else 230.0,
            "pd_mw": r[2],
            "pg_mw": 0.0,  # filled below from gen data
            # AC-specific fields (MATPOWER bus columns)
            "qd_mvar": r[3] if len(r) > 3 else 0.0,
            "gs_mw": r[4] if len(r) > 4 else 0.0,
            "bs_mvar": r[5] if len(r) > 5 else 0.0,
            "vm_pu": r[7] if len(r) > 7 else 1.0,
            "va_deg": r[8] if len(r) > 8 else 0.0,
            "vmax_pu": r[11] if len(r) > 11 else 1.1,
            "vmin_pu": r[12] if len(r) > 12 else 0.9,
        })

    # --- Generators ---
    # Build cost lookup (index-matched to raw_gen)
    # Linearise quadratic gencost: C(pg) = c2·pg² + c1·pg + c0
    # Average marginal cost over [0, pg_max] = c1 + c2·pg_max
    # A small index-based perturbation (1e-6 $/MWh) breaks LP degeneracy
    # when multiple generators share identical linearised costs, ensuring
    # all solvers converge to the same unique optimal vertex.
    gen_costs_linear = []
    for i, g in enumerate(raw_gen):
        pg_max = g[8] if len(g) > 8 else g[1]
        if i < len(raw_gencost):
            gc = raw_gencost[i]
            # gencost type 2 (polynomial, n=3): [type, startup, shutdown, n, c2, c1, c0]
            c2 = gc[4] if len(gc) > 5 else 0.0
            c1 = gc[5] if len(gc) > 5 else 20.0
            cost = c1 + c2 * max(pg_max, 1.0)
        else:
            cost = 20.0
        cost += i * 1e-4  # tiebreaker: ensures unique merit order
        gen_costs_linear.append(cost)

    generators = []
    gen_at_bus = {}  # bus_new_id → generator index (for pg_mw backfill)
    for i, g in enumerate(raw_gen):
        orig_bus = int(g[0])
        new_bus = bus_map[orig_bus]
        pg_max = g[8] if len(g) > 8 else g[1]  # Pmax (col 9) or Pg fallback
        pg_mw = g[1]  # current dispatch (Pg)

        generators.append({
            "bus": new_bus,
            "pg_min": g[9] if len(g) > 9 else 0.0,  # Pmin
            "pg_max": max(pg_max, 1.0),  # avoid zero-capacity
            "cost_mwh": gen_costs_linear[i],
            "fuel": f"Gen{new_bus}",
            "pg_mw": pg_mw,
            # AC-specific fields (MATPOWER gen columns)
            "qg_mvar": g[2] if len(g) > 2 else 0.0,
            "qmax_mvar": g[3] if len(g) > 3 else 999.0,
            "qmin_mvar": g[4] if len(g) > 4 else -999.0,
            "vg_pu": g[5] if len(g) > 5 else 1.0,
        })
        gen_at_bus[new_bus] = i
        buses[new_bus]["pg_mw"] = pg_mw

    # --- Lines / Branches ---
    lines = []
    for r in raw_branch:
        from_bus = bus_map[int(r[0])]
        to_bus = bus_map[int(r[1])]
        x_pu = r[3]
        if x_pu <= 0:
            x_pu = 1e-4  # avoid division by zero
        rate_a = r[5] if len(r) > 5 else 0.0
        # MATPOWER convention: rateA=0 means unlimited (no thermal limit).
        # Use 9900 MW default to keep the LP well-conditioned (unique angles).
        if rate_a <= 0:
            rate_a = 9900.0

        # Transformer tap ratio (col 8) and phase shift (col 9)
        # MATPOWER convention: tap=0 means "not a transformer" → treat as 1.0
        tap_raw = r[8] if len(r) > 8 else 0.0
        tap = tap_raw if tap_raw != 0.0 else 1.0
        shift_deg = r[9] if len(r) > 9 else 0.0

        lines.append({
            "from": from_bus,
            "to": to_bus,
            "r_pu": r[2],
            "x_pu": x_pu,
            "b_pu": r[4] if len(r) > 4 else 0.0,
            "rate_mw": rate_a,
            "tap": tap,
            "shift_deg": shift_deg,
        })

    total_load = sum(b["pd_mw"] for b in buses)
    n_lines = len(lines)

    # Detect parallel lines
    from_to_pairs = [(l["from"], l["to"]) for l in lines]
    parallel_pairs = [p for p in set(from_to_pairs) if from_to_pairs.count(p) > 1]

    # System name from case_name
    num_str = re.search(r"\d+", case_name)
    sys_num = num_str.group() if num_str else case_name
    name = f"IEEE {sys_num}-bus"

    return {
        "name": name,
        "num_buses": n,
        "num_lines": n_lines,
        "num_generators": len(generators),
        "base_mva": base_mva,
        "slack_bus": slack_bus,
        "buses": buses,
        "lines": lines,
        "generators": generators,
        "total_load_mw": total_load,
        "expected_cycles": n_lines - n + 1,
        "max_angle_diff_deg": 90.0,  # generous for large systems
        "parallel_line_pairs": parallel_pairs,
    }


def ieee_118bus() -> dict:
    """IEEE 118-bus system (AEP, 1962).

    Loaded from MATPOWER ``case118.m``.  Requires ``pip install matpower``.

    118 buses, 186 lines (7 parallel pairs), 54 generators.
    Total load: 4242 MW.  Slack bus: 69 (remapped to 0-indexed).
    """
    return _parse_matpower_case("case118")


def ieee_300bus() -> dict:
    """IEEE 300-bus system (IEEE CDF).

    Loaded from MATPOWER ``case300.m``.  Requires ``pip install matpower``.

    300 buses (non-sequential, remapped to 0-indexed), 411 lines, 69 generators.
    Total load: 23526 MW.  Slack bus: 7049 (remapped to 0-indexed).
    """
    return _parse_matpower_case("case300")


def _assign_synthetic_costs(ieee_data: dict) -> dict:
    """Assign merit-order costs when source data has uniform generator costs.

    PEGASE cases ship with c1=1 $/MWh for every generator, making the DCOPF
    objective degenerate.  This function assigns realistic costs so that
    larger units (baseload) are cheaper and smaller units (peakers) are
    more expensive — the standard merit-order assumption.

    Range: 10–50 $/MWh, with a tiny index perturbation to guarantee
    uniqueness and a deterministic optimal dispatch.
    """
    costs = [g["cost_mwh"] for g in ieee_data["generators"]]
    if len(costs) > 1 and (max(costs) - min(costs)) > 5.0:
        return ieee_data  # already diverse (spread > 5 $/MWh)

    gens = ieee_data["generators"]
    n = len(gens)
    # Rank by Pmax descending: largest → cheapest
    ranked = sorted(range(n), key=lambda i: gens[i]["pg_max"], reverse=True)
    for rank, idx in enumerate(ranked):
        gens[idx]["cost_mwh"] = 10.0 + 40.0 * rank / max(n - 1, 1) + 0.001 * idx
    return ieee_data


def pegase_1354bus() -> dict:
    """PEGASE 1354-bus European HV network (380/220 kV).

    Loaded from MATPOWER ``case1354pegase.m``.  Requires ``pip install matpower``.

    1354 buses, 1991 lines, 260 generators.
    Costs are synthetic (merit-order by capacity) since PEGASE ships c1=1 for all.
    """
    data = _parse_matpower_case("case1354pegase")
    data["name"] = "PEGASE 1354-bus"
    return _assign_synthetic_costs(data)


def pegase_2869bus() -> dict:
    """PEGASE 2869-bus European HV network.

    Loaded from MATPOWER ``case2869pegase.m``.  Requires ``pip install matpower``.

    2869 buses, 4582 lines, 510 generators.
    Costs are synthetic (merit-order by capacity).
    """
    data = _parse_matpower_case("case2869pegase")
    data["name"] = "PEGASE 2869-bus"
    return _assign_synthetic_costs(data)


def pegase_9241bus() -> dict:
    """PEGASE 9241-bus full European transmission network.

    Loaded from MATPOWER ``case9241pegase.m``.  Requires ``pip install matpower``.

    9241 buses, 16049 lines, 1445 generators.
    Costs are synthetic (merit-order by capacity).
    """
    data = _parse_matpower_case("case9241pegase")
    data["name"] = "PEGASE 9241-bus"
    return _assign_synthetic_costs(data)


def pegase_13659bus() -> dict:
    """PEGASE 13659-bus full European transmission network.

    Loaded from MATPOWER ``case13659pegase.m``.  Requires ``pip install matpower``.

    13659 buses, 20467 lines, 4092 generators.
    Total load: 381432 MW.  Slack bus: 0.
    Costs are synthetic (merit-order by capacity).
    """
    data = _parse_matpower_case("case13659pegase")
    data["name"] = "PEGASE 13659-bus"
    return _assign_synthetic_costs(data)


def compute_dc_power_flow_reference(ieee_data: dict) -> dict:
    """Compute DC power flow reference solution analytically.

    Uses B⁻¹ method with transformer tap ratios and phase shifters
    (following MATPOWER ``makeBdc`` convention):
      b_ij = 1 / (x_ij * tap_ij)
      B[i,j] = -b_ij  (off-diagonal)
      B[i,i] = sum(b_ij for all lines connected to bus i)
      Pbusinj = phase shift power injections
      P_inj = P_gen - P_load - Pbusinj
      θ = B_reduced⁻¹ · P_inj_reduced  (slack bus removed)
      flow(ℓ) = b_ℓ * (θ_from - θ_to - shift_ℓ) in p.u., then × base_mva

    Returns dict with 'angles_deg', 'line_flows_mw', 'slack_gen_mw'.
    """
    import math

    n = ieee_data["num_buses"]
    base_mva = ieee_data["base_mva"]
    buses = ieee_data["buses"]
    lines_data = ieee_data["lines"]
    generators = ieee_data["generators"]

    # Find slack bus
    slack = 0
    for b in buses:
        if b["bus_type"] == "slack":
            slack = b["bus_id"]
            break

    # Build B matrix (admittance, susceptance only) with tap ratios
    B = np.zeros((n, n))
    Pbusinj = np.zeros(n)  # phase shift power injection (p.u.)
    for line in lines_data:
        i, j = line["from"], line["to"]
        x = line["x_pu"]
        if x <= 0:
            continue
        tap = line.get("tap", 1.0)
        shift_rad = math.radians(line.get("shift_deg", 0.0))
        b_val = 1.0 / (x * tap)
        B[i, j] -= b_val
        B[j, i] -= b_val
        B[i, i] += b_val
        B[j, j] += b_val
        # Phase shift injection (MATPOWER makeBdc convention)
        Pfinj = -b_val * shift_rad
        Pbusinj[i] += Pfinj
        Pbusinj[j] -= Pfinj

    # Net injection vector (P_gen - P_load - Pbusinj) in p.u.
    P_inj = np.zeros(n)
    for b in buses:
        P_inj[b["bus_id"]] -= b["pd_mw"] / base_mva
    P_inj -= Pbusinj

    # Add generator injections (for non-slack generators with known dispatch)
    gen_dispatch = {}
    for g in generators:
        bus = g["bus"]
        if bus == slack:
            continue
        # For DCOPF economic dispatch, we need to solve an OPF
        # For simple DC power flow, we use known generation
        pg = g.get("pg_mw", g.get("pg_max", 0.0))
        if pg is None or pg == 0:
            # Use cost-based merit order - cheapest first
            pg = 0.0
        P_inj[bus] += pg / base_mva
        gen_dispatch[bus] = pg

    # Slack bus absorbs mismatch
    slack_gen = -(sum(P_inj) - P_inj[slack]) * base_mva
    # Correct: slack gen = total_load - sum(other_gen)
    total_load = sum(b["pd_mw"] for b in buses)
    total_other_gen = sum(gen_dispatch.values())
    slack_gen = total_load - total_other_gen
    P_inj[slack] = slack_gen / base_mva - buses[slack]["pd_mw"] / base_mva - Pbusinj[slack]
    gen_dispatch[slack] = slack_gen

    # Remove slack bus row/col
    keep = [i for i in range(n) if i != slack]
    B_red = B[np.ix_(keep, keep)]
    P_red = P_inj[keep]

    # Solve for angles
    theta_red = np.linalg.solve(B_red, P_red)

    # Full angle vector (slack = 0)
    theta = np.zeros(n)
    for idx, bus_idx in enumerate(keep):
        theta[bus_idx] = theta_red[idx]

    # Compute line flows
    line_flows_mw = []
    for line in lines_data:
        i, j = line["from"], line["to"]
        x = line["x_pu"]
        if x <= 0:
            line_flows_mw.append(0.0)
            continue
        tap = line.get("tap", 1.0)
        shift_rad = math.radians(line.get("shift_deg", 0.0))
        b_val = 1.0 / (x * tap)
        flow_pu = b_val * (theta[i] - theta[j] - shift_rad)
        line_flows_mw.append(flow_pu * base_mva)

    # Convert angles to degrees
    angles_deg = np.degrees(theta)

    return {
        "angles_deg": angles_deg.tolist(),
        "line_flows_mw": line_flows_mw,
        "gen_dispatch_mw": gen_dispatch,
        "slack_gen_mw": slack_gen,
    }


def compute_dc_opf_reference(ieee_data: dict) -> dict:
    """Compute DC Optimal Power Flow reference using scipy LP solver.

    Solves the same problem as ESFEX: minimize generation cost subject to
    DC power flow constraints (with tap ratios and phase shifts) and line
    thermal limits.

    Formulation (LP), following MATPOWER ``makeBdc`` convention:
        b_l = S_base / (x_l * tap_l)
        min  sum(cost_g * pg_g)
        s.t. sum(pg at bus b) - sum_j(B_bj * S_base * theta_j) = pd_b + Pbusinj_b
             -rate_l <= b_l * (theta_i - theta_j - shift_l) <= rate_l
             pg_min_g <= pg_g <= pg_max_g
             theta_slack = 0

    Returns dict with 'angles_deg', 'line_flows_mw', 'gen_dispatch_mw',
    'gen_dispatch_list', 'total_cost'.
    """
    import math

    from scipy.optimize import linprog

    n = ieee_data["num_buses"]
    base_mva = ieee_data["base_mva"]
    buses = ieee_data["buses"]
    lines_data = ieee_data["lines"]
    generators = ieee_data["generators"]
    n_gen = len(generators)
    n_lines = len(lines_data)

    # Find slack bus
    slack = 0
    for b in buses:
        if b["bus_type"] == "slack":
            slack = b["bus_id"]
            break

    # Build B matrix (susceptance) with tap ratios, and phase shift injection
    B = np.zeros((n, n))
    Pbusinj = np.zeros(n)  # phase shift power injection (p.u.)
    for line in lines_data:
        i, j = line["from"], line["to"]
        x = line["x_pu"]
        if x <= 0:
            continue
        tap = line.get("tap", 1.0)
        shift_rad = math.radians(line.get("shift_deg", 0.0))
        b_val = 1.0 / (x * tap)
        B[i, j] -= b_val
        B[j, i] -= b_val
        B[i, i] += b_val
        B[j, j] += b_val
        # Phase shift injection (MATPOWER makeBdc convention)
        Pfinj = -b_val * shift_rad
        Pbusinj[i] += Pfinj
        Pbusinj[j] -= Pfinj

    # Decision variables: x = [pg_0, ..., pg_{G-1}, theta_0, ..., theta_{N-1}]
    n_vars = n_gen + n

    # Objective: min sum(cost_g * pg_g)
    c = np.zeros(n_vars)
    for g_idx, g in enumerate(generators):
        c[g_idx] = g["cost_mwh"]

    # Equality constraints: power balance at each bus + slack angle = 0
    A_eq = np.zeros((n + 1, n_vars))
    b_eq = np.zeros(n + 1)

    for b_idx in range(n):
        # Generator contributions
        for g_idx, g in enumerate(generators):
            if g["bus"] == b_idx:
                A_eq[b_idx, g_idx] = 1.0
        # Angle contributions: -S_base * B[b, j] * theta_j
        for j in range(n):
            A_eq[b_idx, n_gen + j] = -base_mva * B[b_idx, j]
        # RHS: demand + phase shift injection (MW)
        b_eq[b_idx] = buses[b_idx]["pd_mw"] + base_mva * Pbusinj[b_idx]

    # Slack angle = 0
    A_eq[n, n_gen + slack] = 1.0
    b_eq[n] = 0.0

    # Inequality constraints: line thermal limits (2 per rated line)
    # Only include lines with finite ratings (rate_mw > 0); rate=0 = unlimited
    rated_lines = [(l_idx, line) for l_idx, line in enumerate(lines_data)
                   if line["rate_mw"] > 0 and line["x_pu"] > 0]
    n_rated = len(rated_lines)

    if n_rated > 0:
        A_ub = np.zeros((2 * n_rated, n_vars))
        b_ub = np.zeros(2 * n_rated)

        for row_idx, (l_idx, line) in enumerate(rated_lines):
            i, j = line["from"], line["to"]
            tap = line.get("tap", 1.0)
            shift_rad = math.radians(line.get("shift_deg", 0.0))
            b_l = base_mva / (line["x_pu"] * tap)  # MW/radian
            rate = line["rate_mw"]

            # flow = b_l * (theta_i - theta_j - shift_rad) <= rate
            A_ub[2 * row_idx, n_gen + i] = b_l
            A_ub[2 * row_idx, n_gen + j] = -b_l
            b_ub[2 * row_idx] = rate + b_l * shift_rad

            # -flow <= rate
            A_ub[2 * row_idx + 1, n_gen + i] = -b_l
            A_ub[2 * row_idx + 1, n_gen + j] = b_l
            b_ub[2 * row_idx + 1] = rate - b_l * shift_rad
    else:
        A_ub = None
        b_ub = None

    # Variable bounds
    bounds = []
    for g in generators:
        pg_min = g.get("pg_min", 0.0)
        bounds.append((pg_min, g["pg_max"]))
    for b_idx in range(n):
        if b_idx == slack:
            bounds.append((0.0, 0.0))
        else:
            bounds.append((-np.pi, np.pi))

    # Solve LP
    import time as _time
    _t_solver = _time.perf_counter()
    result = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq,
                     bounds=bounds, method="highs")
    _solver_time = _time.perf_counter() - _t_solver

    if not result.success:
        raise RuntimeError(f"DC OPF reference failed: {result.message}")

    pg = result.x[:n_gen]
    theta = result.x[n_gen:]

    # Per-bus dispatch dict (for backward compat)
    gen_dispatch = {}
    for g_idx, g in enumerate(generators):
        gen_dispatch[g["bus"]] = pg[g_idx]

    # Per-generator list (matches ieee_data["generators"] order)
    gen_dispatch_list = [float(pg[g_idx]) for g_idx in range(n_gen)]

    # Line flows
    line_flows_mw = []
    for line in lines_data:
        i, j = line["from"], line["to"]
        x = line["x_pu"]
        if x <= 0:
            line_flows_mw.append(0.0)
            continue
        tap = line.get("tap", 1.0)
        shift_rad = math.radians(line.get("shift_deg", 0.0))
        b_l = base_mva / (x * tap)
        flow = b_l * (theta[i] - theta[j] - shift_rad)
        line_flows_mw.append(flow)

    angles_deg = np.degrees(theta).tolist()

    return {
        "angles_deg": angles_deg,
        "line_flows_mw": line_flows_mw,
        "gen_dispatch_mw": gen_dispatch,
        "gen_dispatch_list": gen_dispatch_list,
        "total_cost": result.fun,
        "_solver_time": _solver_time,
    }
