# -*- coding: utf-8 -*-
"""
ESFEX Financial Analysis Engine

Professional-grade post-optimization financial analysis for energy systems.
This module is GUI-independent and can be used programmatically.

Example usage::

    from esfex.models.financial_analysis import (
        FinancialAssumptions,
        compute_system_financials,
        compute_technology_financials,
        run_sensitivity_analysis,
    )

    assumptions = FinancialAssumptions(discount_rate=0.08)
    financials = compute_system_financials("results.h5", assumptions)
    print(f"NPV: ${financials.npv_total:,.0f}")
    print(f"IRR: {financials.project_irr:.1%}")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# =====================================================================
# Dataclasses
# =====================================================================


@dataclass
class FinancialAssumptions:
    """User-configurable financial parameters."""

    # Capital structure
    debt_fraction: float = 0.60
    cost_of_debt: float = 0.05
    cost_of_equity: float = 0.12
    debt_tenor: int = 15

    # Tax & depreciation
    tax_rate: float = 0.25
    depreciation_method: str = "straight_line"  # or "macrs"
    depreciation_years: int = 20
    itc_rate: float = 0.0
    ptc_rate: float = 0.0  # $/MWh

    # Revenue
    ppa_price: float = 0.0  # $/MWh; 0 = use nodal prices from HDF5
    ppa_escalation: float = 0.02
    capacity_payment: float = 0.0  # $/MW-year
    rec_price: float = 0.0  # $/MWh

    # Environmental
    carbon_price: float = 0.0  # $/tCO2
    carbon_price_escalation: float = 0.02

    # Other
    insurance_rate: float = 0.005  # fraction of CAPEX/year
    salvage_fraction: float = 0.05
    discount_rate: float = 0.08


@dataclass
class TechnologyFinancials:
    """Per-technology financial breakdown."""

    name: str = ""
    tech_type: str = ""  # "generator", "battery", "technology", "battery_technology"
    fuel_type: str = ""

    # Investment
    capex_total: float = 0.0
    installed_mw: float = 0.0

    # Production
    generation_mwh: float = 0.0  # total over all years
    annual_generation: np.ndarray = field(default_factory=lambda: np.array([]))
    capacity_factor: float = 0.0

    # Revenue
    revenue_total: float = 0.0
    annual_revenue: np.ndarray = field(default_factory=lambda: np.array([]))
    average_selling_price: float = 0.0

    # Costs
    fuel_cost_total: float = 0.0
    om_cost_total: float = 0.0
    startup_cost_total: float = 0.0
    co2_cost_total: float = 0.0

    # Metrics
    lcoe: float = 0.0
    valcoe: float = 0.0
    roi: float = 0.0
    contribution_to_npv: float = 0.0

    # Storage-specific
    lcos: float = float("nan")
    arbitrage_revenue: float = float("nan")
    degradation_cost: float = float("nan")


@dataclass
class SystemFinancials:
    """Complete system-level financial analysis results."""

    # NPV decomposition ($)
    npv_capex: float = 0.0
    npv_fuel: float = 0.0
    npv_om: float = 0.0
    npv_decommissioning: float = 0.0
    npv_penalties: float = 0.0
    npv_revenue: float = 0.0
    npv_tax: float = 0.0
    npv_tax_benefits: float = 0.0
    npv_salvage: float = 0.0
    npv_total: float = 0.0

    # Annual cash flows
    cash_flows: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Return metrics
    project_irr: float = 0.0
    equity_irr: float = 0.0
    mirr: float = 0.0
    payback_simple: float = float("inf")
    payback_discounted: float = float("inf")
    wacc: float = 0.0
    profitability_index: float = 0.0

    # Debt metrics
    dscr_annual: np.ndarray = field(default_factory=lambda: np.array([]))
    dscr_min: float = 0.0
    llcr: float = 0.0
    cfads: np.ndarray = field(default_factory=lambda: np.array([]))

    # System cost metrics
    lcoe_system: float = 0.0
    lcoe_by_tech: dict = field(default_factory=dict)
    lcos_by_battery: dict = field(default_factory=dict)
    valcoe_by_tech: dict = field(default_factory=dict)

    # Per-technology
    tech_financials: dict = field(default_factory=dict)

    # New-investment (greenfield) metrics — economics attributable ONLY to the
    # capacity expansion expressed as "Investment <tech>" generation series
    # (revenue from their output, opex borrowed from a same-fuel existing
    # unit, their own CAPEX). Unlike the system-level IRR, these are not
    # inflated by revenue from pre-existing sunk-cost plants.
    investment_npv: float = 0.0
    investment_irr: float = 0.0
    investment_equity_irr: float = 0.0
    investment_payback: float = float("inf")
    investment_capex: float = 0.0
    investment_lcoe: float = 0.0


@dataclass
class SensitivityResult:
    """Sensitivity / tornado analysis results."""

    base_npv: float = 0.0
    base_irr: float = 0.0
    sweeps: dict = field(default_factory=dict)   # var -> [(pct, npv, irr), ...]
    tornado: dict = field(default_factory=dict)  # var -> (npv_low, npv_high)
    breakeven: dict = field(default_factory=dict)  # var -> break-even value


@dataclass
class MonteCarloResult:
    """Monte Carlo simulation results."""

    n_samples: int = 0
    npv_samples: np.ndarray = field(default_factory=lambda: np.array([]))
    irr_samples: np.ndarray = field(default_factory=lambda: np.array([]))
    npv_mean: float = 0.0
    npv_std: float = 0.0
    npv_p5: float = 0.0
    npv_p25: float = 0.0
    npv_p50: float = 0.0
    npv_p75: float = 0.0
    npv_p95: float = 0.0
    npv_var_5: float = 0.0
    npv_cvar_5: float = 0.0
    irr_mean: float = 0.0
    irr_std: float = 0.0


# =====================================================================
# Financial Helpers
# =====================================================================


def _crf(rate: float, years: int) -> float:
    """Capital Recovery Factor."""
    if rate <= 0:
        return 1.0 / max(years, 1)
    return rate * (1 + rate) ** years / ((1 + rate) ** years - 1)


def _compute_irr(cash_flows: list[float], tol: float = 1e-6) -> float:
    """Internal Rate of Return via bisection.

    Returns 0.0 for a degenerate (empty or all-zero) cash-flow stream,
    where NPV is identically zero and no meaningful rate exists. For
    non-conventional flows with multiple sign changes the bracket
    [-50%, 500%] yields one deterministic root.
    """
    if not cash_flows or all(cf == 0.0 for cf in cash_flows):
        return 0.0
    if all(cf >= 0.0 for cf in cash_flows):
        # No investment outflow → return on investment is undefined.
        # (Previously this fell through to the bracket ceiling and showed
        # as a spurious 500% IRR for operational/no-capex runs.)
        return float("nan")
    lo, hi = -0.5, 5.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        npv = sum(cf / (1 + mid) ** t for t, cf in enumerate(cash_flows))
        if abs(npv) < tol:
            return mid
        if npv > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _compute_mirr(
    cash_flows: list[float], finance_rate: float, reinvest_rate: float,
) -> float:
    """Modified Internal Rate of Return."""
    n = len(cash_flows) - 1
    if n <= 0:
        return 0.0
    neg_pv = sum(
        cf / (1 + finance_rate) ** t
        for t, cf in enumerate(cash_flows)
        if cf < 0
    )
    pos_fv = sum(
        cf * (1 + reinvest_rate) ** (n - t)
        for t, cf in enumerate(cash_flows)
        if cf > 0
    )
    if neg_pv == 0 or pos_fv <= 0:
        return 0.0
    return (pos_fv / abs(neg_pv)) ** (1.0 / n) - 1.0


def _compute_npv(cash_flows: list[float], rate: float) -> float:
    """Net Present Value."""
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(cash_flows))


def _payback(cash_flows: list[float], discounted: bool = False,
             rate: float = 0.0) -> float:
    """Simple or discounted payback period."""
    cumulative = 0.0
    for t, cf in enumerate(cash_flows):
        dcf = cf / (1 + rate) ** t if discounted else cf
        prev = cumulative
        cumulative += dcf
        if cumulative >= 0 and prev < 0 and t > 0:
            frac = -prev / dcf if dcf != 0 else 0
            return (t - 1) + frac
    return float("inf")


def _depreciation_schedule(
    capex: float, method: str, years: int, lifetime: int,
) -> np.ndarray:
    """Compute annual depreciation amounts."""
    schedule = np.zeros(lifetime)
    if method == "macrs":
        # 5-year MACRS percentages
        macrs_5 = [0.20, 0.32, 0.192, 0.1152, 0.1152, 0.0576]
        for i, pct in enumerate(macrs_5):
            if i < lifetime:
                schedule[i] = capex * pct
    else:  # straight_line
        dep_years = min(years, lifetime)
        if dep_years > 0:
            annual = capex / dep_years
            schedule[:dep_years] = annual
    return schedule


def _debt_service(
    principal: float, rate: float, tenor: int, lifetime: int,
) -> np.ndarray:
    """Compute annual debt service (constant annuity)."""
    service = np.zeros(lifetime)
    if principal <= 0 or tenor <= 0:
        return service
    annuity = principal * _crf(rate, tenor) if rate > 0 else principal / tenor
    service[:min(tenor, lifetime)] = annuity
    return service


def _vintage_depreciation(
    annual_capex: np.ndarray, method: str, dep_years: int, n_years: int,
) -> np.ndarray:
    """Aggregate depreciation across capex vintages.

    Each year's investment is depreciated starting from its own in-service
    year, so multi-year build-outs get the correct tax timing instead of
    depreciating the whole stack from year 0.
    """
    sched = np.zeros(n_years)
    for v in range(n_years):
        cap_v = float(annual_capex[v])
        if cap_v <= 0:
            continue
        s = _depreciation_schedule(cap_v, method, dep_years, n_years - v)
        sched[v:] += s
    return sched


def _vintage_debt_service(
    annual_capex: np.ndarray, debt_fraction: float, rate: float,
    tenor: int, n_years: int,
) -> np.ndarray:
    """Aggregate debt service across capex vintages (debt drawn per vintage)."""
    ds = np.zeros(n_years)
    for v in range(n_years):
        principal = float(annual_capex[v]) * debt_fraction
        if principal <= 0:
            continue
        s = _debt_service(principal, rate, tenor, n_years - v)
        ds[v:] += s
    return ds


def _fuel_opex_map(
    gen_configs: list[dict], n_nodes: int,
) -> dict[str, tuple[float, float, float]]:
    """Map fuel -> (fuel_cost, fixed_cost, maintenance_cost) in $/MWh.

    Investment-technology configs in HDF5 carry no operating costs, so the
    per-MWh opex of a new "Investment <tech>" unit is borrowed from the first
    existing generator of the same fuel (the representative unit value is the
    max across the per-node array, since unused nodes are zero-filled).
    """
    out: dict[str, tuple[float, float, float]] = {}
    for cfg in gen_configs:
        fuel = str(cfg.get("fuel", cfg.get("fuel_type", ""))).strip()
        if not fuel or fuel in out:
            continue
        fc = float(np.max(_as_array(cfg.get("fuel_cost", 0.0), n_nodes)))
        fx = float(np.max(_as_array(cfg.get("fixed_cost", 0.0), n_nodes)))
        mc = float(np.max(_as_array(cfg.get("maintenance_cost", 0.0), n_nodes)))
        out[fuel] = (fc, fx, mc)
    return out


def _battery_opex_rate(bat_configs: list[dict], n_nodes: int) -> float:
    """Representative battery throughput O&M ($/MWh) from existing batteries.

    Battery-technology configs carry no maintenance cost, so a new
    "Investment <bat>" unit's O&M is borrowed from the existing batteries.
    """
    rate = 0.0
    for cfg in bat_configs:
        rate = max(
            rate, float(np.max(_as_array(cfg.get("maintenance_cost", 0.0), n_nodes)))
        )
    return rate


def _investment_cashflow_metrics(
    inv_revenue: np.ndarray,
    inv_fuel: np.ndarray,
    inv_om: np.ndarray,
    inv_capex: np.ndarray,
    inv_generation: np.ndarray,
    assumptions: "FinancialAssumptions",
    discount: float,
    n_years: int,
) -> dict:
    """Project metrics (NPV, IRR, payback, LCOE) for the new-investment stream
    only: revenue and operating cost of the built capacity against its CAPEX,
    with the same tax / depreciation / debt machinery as the system view.
    """
    opex = inv_fuel + inv_om
    cum_capex = np.cumsum(inv_capex)
    insurance = cum_capex * assumptions.insurance_rate
    dep = _vintage_depreciation(
        inv_capex, assumptions.depreciation_method,
        assumptions.depreciation_years, n_years,
    )
    itc = inv_capex * assumptions.itc_rate
    ds = _vintage_debt_service(
        inv_capex, assumptions.debt_fraction, assumptions.cost_of_debt,
        assumptions.debt_tenor, n_years,
    )
    total_capex = float(np.sum(inv_capex))
    salvage = np.zeros(n_years)
    if n_years > 0:
        salvage[-1] = total_capex * assumptions.salvage_fraction

    taxable = inv_revenue - opex - insurance - dep
    tax = np.maximum(taxable * assumptions.tax_rate, 0.0)
    net_cf = inv_revenue - opex - insurance - inv_capex - tax + itc + salvage

    disc = np.array([1 / (1 + discount) ** t for t in range(n_years)])
    npv = float(np.sum(net_cf * disc))
    project_cf = net_cf.tolist()
    irr = _compute_irr(project_cf) if n_years > 1 else 0.0
    equity_irr = _compute_irr((net_cf - ds).tolist()) if n_years > 1 else 0.0
    payback = _payback(project_cf)

    gen_disc = float(np.sum(inv_generation * disc))
    cost_disc = (
        float(np.sum((inv_fuel + inv_om + insurance) * disc))
        + float(np.sum(inv_capex * disc))
    )
    lcoe = cost_disc / gen_disc if gen_disc > 0 else float("inf")

    return {
        "npv": npv, "irr": irr, "equity_irr": equity_irr,
        "payback": payback, "capex": total_capex, "lcoe": lcoe,
    }


# =====================================================================
# HDF5 Reading Helpers
# =====================================================================


def _load_system_from_h5(h5_path: Path | str) -> dict:
    """Load system configuration, scenario list, and metadata from HDF5.

    Returns dict with keys: gen_configs, bat_configs, tech_configs,
    bat_tech_configs, years, scenarios, temporal_res, num_nodes,
    discount_rate, penalties.
    """
    import h5py

    h5_path = Path(h5_path)
    info: dict = {
        "gen_configs": [],
        "bat_configs": [],
        "tech_configs": [],
        "bat_tech_configs": [],
        "years": [],
        "scenarios": {},
        "temporal_res": 1,
        "num_nodes": 1,
        "discount_rate": 0.08,
        "penalties": {},
    }

    with h5py.File(h5_path, "r") as f:
        info["temporal_res"] = int(f.attrs.get("temporal_resolution_hours", 1))
        info["num_nodes"] = int(f.attrs.get("num_nodes", 1))

        # Years
        if "summary_results" in f and "year" in f["summary_results"]:
            info["years"] = sorted(set(int(y) for y in f["summary_results/year"][:]))

        # Scenarios
        if "detailed_results" in f:
            for key in sorted(f["detailed_results"].keys()):
                grp = f["detailed_results"][key]
                year = int(grp.attrs.get("year", 0))
                if year > 0:
                    info["scenarios"][year] = key

        # Generators
        if "system_configuration/generators" in f:
            gen_grp = f["system_configuration/generators"]
            n = int(gen_grp.attrs.get("num_generators", 0))
            for i in range(n):
                gk = f"generator_{i}"
                if gk in gen_grp:
                    info["gen_configs"].append(dict(gen_grp[gk].attrs))

        # Batteries
        if "system_configuration/batteries" in f:
            bat_grp = f["system_configuration/batteries"]
            n = int(bat_grp.attrs.get("num_batteries", 0))
            for i in range(n):
                bk = f"battery_{i}"
                if bk in bat_grp:
                    info["bat_configs"].append(dict(bat_grp[bk].attrs))

        # Technologies
        if "system_configuration/technologies" in f:
            tech_grp = f["system_configuration/technologies"]
            n = int(tech_grp.attrs.get("num_technologies", 0))
            for i in range(n):
                tk = f"technology_{i}"
                if tk in tech_grp:
                    info["tech_configs"].append(dict(tech_grp[tk].attrs))

        # Battery technologies
        if "system_configuration/battery_technologies" in f:
            bt_grp = f["system_configuration/battery_technologies"]
            n = int(bt_grp.attrs.get("num_battery_technologies", 0))
            for i in range(n):
                btk = f"battery_technology_{i}"
                if btk in bt_grp:
                    info["bat_tech_configs"].append(dict(bt_grp[btk].attrs))

    return info


def _load_year_data(h5_path: Path | str, scenario_key: str) -> dict:
    """Load operational data for one year from HDF5.

    Returns dict with generation (per-gen arrays), prices, demand,
    curtailment, load_shed, co2_emissions, battery data, investment data.
    """
    import h5py

    data: dict = {}
    h5_path = Path(h5_path)

    with h5py.File(h5_path, "r") as f:
        grp = f[f"detailed_results/{scenario_key}"]
        data["total_cost"] = float(grp.attrs.get("total_cost", 0.0))

        # Generation
        data["generation"] = {}
        if "generation" in grp:
            for name in grp["generation"]:
                data["generation"][name] = grp["generation"][name][:]

        # Prices
        if "nodal_electricity_prices" in grp:
            data["nodal_prices"] = grp["nodal_electricity_prices"][:]
        if "electricity_prices" in grp:
            data["system_prices"] = grp["electricity_prices"][:]

        # Demand
        if "demand" in grp:
            data["demand"] = grp["demand"][:]

        # Curtailment & load shed
        if "curtailment" in grp:
            data["curtailment"] = grp["curtailment"][:]
        if "loss_load" in grp:
            data["loss_load"] = grp["loss_load"][:]

        # CO2
        if "CO2_emissions" in grp:
            data["co2_emissions"] = grp["CO2_emissions"][:]

        # Startup
        if "gen_startup" in grp:
            data["gen_startup"] = {}
            for name in grp["gen_startup"]:
                data["gen_startup"][name] = grp["gen_startup"][name][:]

        # Battery data
        for key in ("battery_charge", "battery_discharge", "battery_soc"):
            if key in grp:
                data[key] = {}
                for name in grp[key]:
                    data[key][name] = grp[key][name][:]

        # Investments
        for key in ("gen_investment_power", "bat_investment_power",
                     "bat_investment_capacity"):
            if key in grp:
                data[key] = {}
                for name in grp[key]:
                    data[key][name] = grp[key][name][:]

        # LCOE / VALLCOE (pre-computed)
        for key in ("lcoe", "vallcoe", "capacity_factor"):
            if key in grp:
                data[key] = {}
                for name in grp[key]:
                    data[key][name] = grp[key][name][:]

    return data


def _try_load_cost_breakdown(h5_path: Path | str, year: int) -> dict | None:
    """Try to load granular cost breakdown from HDF5.

    Returns dict of cost components or None if not available.
    """
    import h5py

    h5_path = Path(h5_path)
    year_key = f"year_{year}"

    with h5py.File(h5_path, "r") as f:
        if "cost_breakdown" not in f:
            return None
        cbd_grp = f["cost_breakdown"]
        if year_key not in cbd_grp:
            return None
        return dict(cbd_grp[year_key].attrs)


# =====================================================================
# Cost Recalculation Fallback
# =====================================================================


def _recalculate_costs(
    year_data: dict,
    gen_configs: list[dict],
    bat_configs: list[dict],
    temporal_res: int,
) -> dict:
    """Reconstruct cost components from generation × config unit costs.

    Used when /cost_breakdown/ is not present in HDF5.
    """
    costs = {
        "fuel_cost": 0.0,
        "om_cost": 0.0,
        "startup_cost": 0.0,
        "co2_cost": 0.0,
        "revenue": 0.0,
        "load_shedding_cost": 0.0,
        "curtailment_cost": 0.0,
        "battery_cost": 0.0,
    }

    gen_names = sorted(year_data.get("generation", {}).keys())
    nodal_prices = year_data.get("nodal_prices", None)
    system_prices = year_data.get("system_prices", None)
    gen_by_name = _index_configs_by_name(gen_configs)

    for g_idx, gen_name in enumerate(gen_names):
        gen_output = year_data["generation"][gen_name]  # [nodes x hours]
        cfg = _lookup_config(gen_name, gen_by_name, gen_configs, g_idx)
        if not cfg:
            continue

        # Get per-node cost arrays (stored as scalar or list in HDF5 attrs)
        fuel_cost = _as_array(cfg.get("fuel_cost", 0.0), gen_output.shape[0])
        fixed_cost = _as_array(cfg.get("fixed_cost", 0.0), gen_output.shape[0])
        maint_cost = _as_array(cfg.get("maintenance_cost", 0.0), gen_output.shape[0])
        su_cost = _as_array(cfg.get("start_up_cost", 0.0), gen_output.shape[0])

        for n in range(gen_output.shape[0]):
            output_n = gen_output[n, :]  # [hours]
            energy_mwh = output_n * temporal_res

            costs["fuel_cost"] += float(np.sum(energy_mwh * fuel_cost[n]))
            costs["om_cost"] += float(np.sum(
                energy_mwh * (fixed_cost[n] + maint_cost[n])
            ))

            # Startup costs
            if "gen_startup" in year_data and gen_name in year_data["gen_startup"]:
                starts = year_data["gen_startup"][gen_name]
                if n < starts.shape[0]:
                    costs["startup_cost"] += float(
                        np.sum(starts[n, :]) * su_cost[n]
                    )

            # Revenue
            if nodal_prices is not None and n < nodal_prices.shape[0]:
                prices_n = nodal_prices[n, :]
            elif system_prices is not None:
                prices_n = system_prices
            else:
                prices_n = np.zeros_like(output_n)
            costs["revenue"] += float(np.sum(energy_mwh * prices_n))

    # Battery costs
    bat_by_name = _index_configs_by_name(bat_configs)
    for b_idx, bat_name in enumerate(
        sorted(year_data.get("battery_discharge", {}).keys())
    ):
        cfg = _lookup_config(bat_name, bat_by_name, bat_configs, b_idx)
        if not cfg:
            continue
        discharge = year_data["battery_discharge"].get(bat_name)
        charge = year_data.get("battery_charge", {}).get(bat_name)
        if discharge is None:
            continue

        maint = _as_array(cfg.get("maintenance_cost", 0.0), discharge.shape[0])
        for n in range(discharge.shape[0]):
            throughput = (discharge[n, :] + (charge[n, :] if charge is not None
                                             else 0.0)) * temporal_res
            costs["battery_cost"] += float(np.sum(throughput * maint[n]))

    return costs


def _as_array(val, size: int) -> np.ndarray:
    """Convert scalar, list, string-encoded list, or array to 1D numpy array."""
    if isinstance(val, (int, float)):
        return np.full(size, float(val))
    # HDF5 attrs may store lists as strings like '[11.2, 0.0, ...]'
    if isinstance(val, (str, bytes)):
        s = val.decode("utf-8") if isinstance(val, bytes) else val
        s = s.strip()
        if s.startswith("["):
            import ast
            val = ast.literal_eval(s)
        else:
            return np.full(size, float(s))
    arr = np.asarray(val, dtype=float).ravel()
    if arr.size == 1:
        return np.full(size, arr[0])
    if arr.size < size:
        return np.pad(arr, (0, size - arr.size), constant_values=arr[-1])
    return arr[:size]


def _h5safe(name: str) -> str:
    """Mirror runner._h5safe: HDF5 dataset names replace ``/`` with `` - ``."""
    return str(name).replace("/", " - ")


def _index_configs_by_name(configs: list[dict]) -> dict[str, dict]:
    """Map an HDF5 dataset/group name to its config dict.

    Generation, startup and investment datasets are written under
    ``_h5safe(name)`` (and duplicates suffixed `` (N)``), while the
    ``system_configuration`` groups store the raw ``name`` attr.  Indexing
    by the h5-safe name lets a dataset key recover *its own* config
    regardless of how h5py orders the config groups on read.
    """
    out: dict[str, dict] = {}
    for cfg in configs:
        raw = str(cfg.get("name", "")).strip()
        if raw:
            out.setdefault(_h5safe(raw), cfg)
    return out


def _lookup_config(
    name: str, by_name: dict[str, dict],
    configs: list[dict], idx: int = -1,
) -> dict:
    """Resolve a dataset's config by name, falling back to position.

    Falls back to positional indexing only when name matching is
    impossible (e.g. legacy files whose configs carry no ``name`` attr),
    preserving behaviour for those files while fixing the common case.
    """
    if name in by_name:
        return by_name[name]
    # strip a `` (N)`` deduplication suffix and retry
    import re
    m = re.match(r"^(.*) \(\d+\)$", name)
    if m and m.group(1) in by_name:
        return by_name[m.group(1)]
    if 0 <= idx < len(configs):
        return configs[idx]
    return {}


def _resolve_invest_cost(
    cfg: dict, tech_configs: list[dict], cost_field: str,
) -> float:
    """Per-MW (or per-MWh) investment cost for an expanding unit.

    Resolution order:
      1. The unit's ``technology`` link → matching investment-technology
         config's ``cost_field`` (the current data model).
      2. The unit's own deprecated ``cost_field`` attr, if positive.
      3. Legacy fallback: the first technology config (preserves the
         pre-fix result for single-technology files with no explicit link).
    """
    tech_name = str(cfg.get("technology", "") or "").strip()
    if tech_name and tech_name.lower() != "none":
        for tc in tech_configs:
            if str(tc.get("name", "")).strip() == tech_name:
                return float(_as_array(tc.get(cost_field, 0.0), 1)[0])
    own = float(_as_array(cfg.get(cost_field, 0.0), 1)[0])
    if own > 0:
        return own
    if tech_configs:
        return float(_as_array(tech_configs[0].get(cost_field, 0.0), 1)[0])
    return 0.0


# =====================================================================
# Core Analysis Functions
# =====================================================================


def compute_system_financials(
    h5_path: Path | str,
    assumptions: FinancialAssumptions | None = None,
) -> SystemFinancials:
    """Compute comprehensive system-level financial analysis from HDF5 results.

    Parameters
    ----------
    h5_path : path to results HDF5 file
    assumptions : financial parameters (uses defaults if None)

    Returns
    -------
    SystemFinancials with NPV decomposition, cash flows, IRR, DSCR, LCOE, etc.
    """
    if assumptions is None:
        assumptions = FinancialAssumptions()

    h5_path = Path(h5_path)
    info = _load_system_from_h5(h5_path)
    years = info["years"]
    n_years = len(years)

    if n_years == 0:
        logger.warning("No years found in %s", h5_path)
        return SystemFinancials()

    # WACC
    wacc = (
        assumptions.debt_fraction * assumptions.cost_of_debt
        * (1 - assumptions.tax_rate)
        + (1 - assumptions.debt_fraction) * assumptions.cost_of_equity
    )
    discount = assumptions.discount_rate if assumptions.discount_rate > 0 else wacc

    # Accumulators
    annual_revenue = np.zeros(n_years)
    annual_fuel = np.zeros(n_years)
    annual_om = np.zeros(n_years)
    annual_startup = np.zeros(n_years)
    annual_penalties = np.zeros(n_years)
    annual_generation = np.zeros(n_years)  # MWh
    annual_co2 = np.zeros(n_years)
    annual_capex = np.zeros(n_years)
    total_capex = 0.0
    # Cumulative installed MW (per node) of each "Investment <tech>" unit,
    # tracked across years so CAPEX is booked on incremental capacity only.
    invest_installed_mw: dict[str, np.ndarray] = {}
    invest_installed_power: dict[str, np.ndarray] = {}   # battery power (MW)
    invest_installed_energy: dict[str, np.ndarray] = {}  # battery energy (MWh)
    tech_by_name = _index_configs_by_name(info["tech_configs"])
    bat_tech_by_name = _index_configs_by_name(info["bat_tech_configs"])
    # New-investment cash-flow streams (Investment <tech>/<bat> units only).
    inv_revenue = np.zeros(n_years)
    inv_fuel = np.zeros(n_years)
    inv_om = np.zeros(n_years)
    inv_capex_stream = np.zeros(n_years)
    inv_generation = np.zeros(n_years)
    fuel_opex = _fuel_opex_map(info["gen_configs"], info["num_nodes"])
    bat_maint_rate = _battery_opex_rate(info["bat_configs"], info["num_nodes"])
    # True for years whose CO2 was already priced by the optimizer
    # (cost_breakdown.co2_emission_cost) — a user carbon price is NOT
    # re-applied to those years to avoid double counting.
    co2_priced = np.zeros(n_years, dtype=bool)

    for y_idx, year in enumerate(years):
        scenario_key = info["scenarios"].get(year)
        if scenario_key is None:
            continue

        year_data = _load_year_data(h5_path, scenario_key)

        # Try granular cost breakdown first
        cbd = _try_load_cost_breakdown(h5_path, year)
        if cbd is not None:
            annual_fuel[y_idx] = cbd.get("fuel_cost", 0.0)
            annual_om[y_idx] = (
                cbd.get("fixed_om_cost", 0.0)
                + cbd.get("maintenance_cost", 0.0)
                + cbd.get("battery_maintenance_cost", 0.0)
                + cbd.get("battery_degradation_cost", 0.0)
            )
            annual_startup[y_idx] = cbd.get("startup_cost", 0.0)
            co2_priced[y_idx] = "co2_emission_cost" in cbd
            annual_penalties[y_idx] = (
                cbd.get("load_shedding_cost", 0.0)
                + cbd.get("curtailment_cost", 0.0)
                + cbd.get("reserve_static_cost", 0.0)
                + cbd.get("reserve_dynamic_cost", 0.0)
                + cbd.get("co2_emission_cost", 0.0)
                + cbd.get("fre_penetration_cost", 0.0)
                + cbd.get("soc_violation_cost", 0.0)
                + cbd.get("inertia_cost", 0.0)
                + cbd.get("spillage_cost", 0.0)
                + cbd.get("reservoir_spillage_cost", 0.0)
                + cbd.get("rooftop_curtailment_cost", 0.0)
                + cbd.get("transfer_margin_cost", 0.0)
            )
        else:
            # Fallback: recalculate from generation × config
            recalc = _recalculate_costs(
                year_data, info["gen_configs"], info["bat_configs"],
                info["temporal_res"],
            )
            annual_fuel[y_idx] = recalc["fuel_cost"]
            annual_om[y_idx] = recalc["om_cost"] + recalc["battery_cost"]
            annual_startup[y_idx] = recalc["startup_cost"]
            annual_revenue[y_idx] = recalc["revenue"]

        # Generation total
        for gen_name, gen_arr in year_data.get("generation", {}).items():
            gen_mwh = float(np.sum(gen_arr)) * info["temporal_res"]
            annual_generation[y_idx] += gen_mwh

        # Revenue from prices if not from cost breakdown
        if cbd is not None or annual_revenue[y_idx] == 0:
            rev = 0.0
            nodal_prices = year_data.get("nodal_prices")
            sys_prices = year_data.get("system_prices")
            for gen_name, gen_arr in year_data.get("generation", {}).items():
                for n in range(gen_arr.shape[0]):
                    output = gen_arr[n, :] * info["temporal_res"]
                    if assumptions.ppa_price > 0:
                        price = assumptions.ppa_price * (
                            1 + assumptions.ppa_escalation
                        ) ** y_idx
                        rev += float(np.sum(output)) * price
                    elif nodal_prices is not None and n < nodal_prices.shape[0]:
                        rev += float(np.sum(output * nodal_prices[n, :]))
                    elif sys_prices is not None:
                        rev += float(np.sum(output * sys_prices))

            annual_revenue[y_idx] = rev

        # Investment costs ($ from MW × $/MW) — each unit charged at its
        # OWN technology's invest_cost (see _resolve_invest_cost).
        gen_by_name = _index_configs_by_name(info["gen_configs"])
        inv_data = year_data.get("gen_investment_power", {})
        for gen_name, inv_arr in inv_data.items():
            inv_mw = float(np.sum(inv_arr))
            if inv_mw > 0:
                cfg = _lookup_config(gen_name, gen_by_name, info["gen_configs"])
                cost_per_mw = _resolve_invest_cost(
                    cfg, info["tech_configs"], "invest_cost",
                )
                inv_cost = inv_mw * cost_per_mw
                annual_capex[y_idx] += inv_cost
                total_capex += inv_cost

        bat_by_name = _index_configs_by_name(info["bat_configs"])
        bat_inv = year_data.get("bat_investment_power", {})
        for bat_name, inv_arr in bat_inv.items():
            inv_mw = float(np.sum(inv_arr))
            if inv_mw > 0:
                bcfg = _lookup_config(bat_name, bat_by_name, info["bat_configs"])
                cost_p = _resolve_invest_cost(
                    bcfg, info["bat_tech_configs"], "invest_cost_power",
                )
                inv_cost = inv_mw * cost_p
                annual_capex[y_idx] += inv_cost
                total_capex += inv_cost

        # Capacity expansion expressed as "Investment <tech>" generation series
        # (runs without a gen_investment_power group). Per-node peak generation
        # is the installed-MW proxy; CAPEX is booked on the INCREMENTAL capacity
        # added each year × the technology's per-node invest_cost.
        for ds_name, gen_arr in year_data.get("generation", {}).items():
            if not ds_name.startswith("Investment "):
                continue
            tcfg = _lookup_config(
                ds_name[len("Investment "):], tech_by_name, info["tech_configs"],
            )
            if not tcfg:
                continue
            n_g = gen_arr.shape[0]
            inv_cost_node = _as_array(tcfg.get("invest_cost", 0.0), n_g)
            peak_node = np.max(gen_arr, axis=1)  # MW per node (peak proxy)
            prev = invest_installed_mw.get(ds_name, np.zeros(n_g))
            incremental = np.maximum(peak_node - prev, 0.0)
            inv_cost = float(np.sum(incremental * inv_cost_node))
            annual_capex[y_idx] += inv_cost
            total_capex += inv_cost
            invest_installed_mw[ds_name] = np.maximum(peak_node, prev)

            # New-investment cash-flow stream: revenue + opex of this unit.
            inv_capex_stream[y_idx] += inv_cost
            energy_tot = float(np.sum(gen_arr)) * info["temporal_res"]
            inv_generation[y_idx] += energy_tot
            fc_rate, fx_rate, mc_rate = fuel_opex.get(
                str(tcfg.get("fuel", "")).strip(), (0.0, 0.0, 0.0)
            )
            inv_fuel[y_idx] += energy_tot * fc_rate
            inv_om[y_idx] += energy_tot * (fx_rate + mc_rate)
            inv_prices_n = year_data.get("nodal_prices")
            inv_sys_p = year_data.get("system_prices")
            for n in range(n_g):
                out_e = gen_arr[n, :] * info["temporal_res"]
                if assumptions.ppa_price > 0:
                    price = assumptions.ppa_price * (
                        1 + assumptions.ppa_escalation
                    ) ** y_idx
                    inv_revenue[y_idx] += float(np.sum(out_e)) * price
                elif inv_prices_n is not None and n < inv_prices_n.shape[0]:
                    inv_revenue[y_idx] += float(np.sum(out_e * inv_prices_n[n, :]))
                elif inv_sys_p is not None:
                    inv_revenue[y_idx] += float(np.sum(out_e * inv_sys_p))

        # Battery capacity expansion as "Investment <bat>" series in
        # battery_discharge/charge/soc. Power MW = peak max(charge, discharge)
        # per node; energy MWh = peak SOC per node. CAPEX = power × invest_cost
        # _power + energy × invest_cost_energy, booked on incremental capacity.
        for ds_name, dis_arr in year_data.get("battery_discharge", {}).items():
            if not ds_name.startswith("Investment "):
                continue
            btcfg = _lookup_config(
                ds_name[len("Investment "):], bat_tech_by_name,
                info["bat_tech_configs"],
            )
            if not btcfg:
                continue
            chg_arr = year_data.get("battery_charge", {}).get(ds_name)
            soc_arr = year_data.get("battery_soc", {}).get(ds_name)
            n_b = dis_arr.shape[0]
            cost_p = _as_array(btcfg.get("invest_cost_power", 0.0), n_b)
            cost_e = _as_array(btcfg.get("invest_cost_energy", 0.0), n_b)
            dis_peak = np.max(dis_arr, axis=1)
            chg_peak = (np.max(chg_arr, axis=1) if chg_arr is not None
                        else np.zeros(n_b))
            power_node = np.maximum(dis_peak, chg_peak)
            energy_node = (np.max(soc_arr, axis=1) if soc_arr is not None
                           else np.zeros(n_b))
            prev_p = invest_installed_power.get(ds_name, np.zeros(n_b))
            prev_e = invest_installed_energy.get(ds_name, np.zeros(n_b))
            incr_p = np.maximum(power_node - prev_p, 0.0)
            incr_e = np.maximum(energy_node - prev_e, 0.0)
            cap_cost = float(np.sum(incr_p * cost_p) + np.sum(incr_e * cost_e))
            annual_capex[y_idx] += cap_cost
            total_capex += cap_cost
            invest_installed_power[ds_name] = np.maximum(power_node, prev_p)
            invest_installed_energy[ds_name] = np.maximum(energy_node, prev_e)

            # Investment stream: arbitrage revenue, throughput O&M, discharge.
            inv_capex_stream[y_idx] += cap_cost
            b_prices = year_data.get("nodal_prices")
            b_sys = year_data.get("system_prices")
            for n in range(n_b):
                dis_e = dis_arr[n, :] * info["temporal_res"]
                inv_generation[y_idx] += float(np.sum(dis_e))
                if b_prices is not None and n < b_prices.shape[0]:
                    inv_revenue[y_idx] += float(np.sum(dis_e * b_prices[n, :]))
                    if chg_arr is not None:
                        chg_e = chg_arr[n, :] * info["temporal_res"]
                        inv_revenue[y_idx] -= float(np.sum(chg_e * b_prices[n, :]))
                elif b_sys is not None:
                    inv_revenue[y_idx] += float(np.sum(dis_e * b_sys))
                throughput = dis_e + (
                    chg_arr[n, :] * info["temporal_res"]
                    if chg_arr is not None else 0.0
                )
                inv_om[y_idx] += float(np.sum(throughput)) * bat_maint_rate

        # CO2 emissions
        co2_arr = year_data.get("co2_emissions")
        if co2_arr is not None:
            annual_co2[y_idx] = float(np.sum(co2_arr)) * info["temporal_res"]

    # --- Compute Financial Metrics ---

    annual_opex = annual_fuel + annual_om + annual_startup
    # Insurance accrues on the capex placed in service so far (cumulative),
    # not on the full final stack from year 0.
    cum_capex = np.cumsum(annual_capex)
    annual_insurance = cum_capex * assumptions.insurance_rate

    # Depreciation — per capex vintage (each tranche from its in-service year)
    dep_schedule = _vintage_depreciation(
        annual_capex, assumptions.depreciation_method,
        assumptions.depreciation_years, n_years,
    )

    # Tax benefits. ITC is booked in the in-service year of each vintage;
    # PTC accrues per MWh generated.
    itc_by_year = annual_capex * assumptions.itc_rate
    ptc_benefit = annual_generation * assumptions.ptc_rate  # per year

    # Carbon tax — a price on emitted CO2 (escalating). Treated as a
    # deductible operating cost that reduces cash flow. Years already priced
    # by the optimizer (co2_priced) are skipped to avoid double counting.
    carbon_cost = np.zeros(n_years)
    if assumptions.carbon_price > 0:
        for y in range(n_years):
            if co2_priced[y]:
                continue
            cprice = assumptions.carbon_price * (
                1 + assumptions.carbon_price_escalation
            ) ** y
            carbon_cost[y] = annual_co2[y] * cprice

    # REC revenue
    rec_rev = np.zeros(n_years)
    if assumptions.rec_price > 0:
        rec_rev = annual_generation * assumptions.rec_price

    # Capacity payments
    cap_rev = np.zeros(n_years)
    if assumptions.capacity_payment > 0:
        # Use total installed capacity (rough estimate)
        total_mw = sum(
            float(_as_array(gc.get("rated_power", 0.0), 1)[0])
            for gc in info["gen_configs"]
        )
        cap_rev[:] = total_mw * assumptions.capacity_payment

    total_revenue = annual_revenue + rec_rev + cap_rev

    # Debt service — per capex vintage (debt drawn as each tranche is built)
    debt_principal = total_capex * assumptions.debt_fraction
    ds = _vintage_debt_service(
        annual_capex, assumptions.debt_fraction,
        assumptions.cost_of_debt, assumptions.debt_tenor, n_years,
    )

    # Tax (carbon tax and operational penalties are deductible operating
    # expenses). annual_penalties already carries the optimizer's CO2 cost
    # for cost_breakdown years, where carbon_cost is 0 (no double count).
    taxable_income = (
        total_revenue - annual_opex - annual_insurance
        - carbon_cost - annual_penalties - dep_schedule
    )
    tax = np.maximum(taxable_income * assumptions.tax_rate, 0.0)

    # Salvage value at end
    salvage = np.zeros(n_years)
    if n_years > 0:
        salvage[-1] = total_capex * assumptions.salvage_fraction

    # Net cash flows
    net_cf = (
        total_revenue
        - annual_opex
        - annual_insurance
        - carbon_cost
        - annual_penalties
        - annual_capex
        - tax
        + ptc_benefit
        + itc_by_year
        + salvage
    )

    # Project cash flows (for IRR)
    project_cf = net_cf.tolist()

    # Equity cash flows (subtract debt service)
    equity_cf = (net_cf - ds).tolist()

    # NPV decomposition
    disc_factors = np.array([1 / (1 + discount) ** t for t in range(n_years)])

    npv_revenue = float(np.sum(total_revenue * disc_factors))
    npv_fuel = float(np.sum(annual_fuel * disc_factors))
    npv_om = float(np.sum((annual_om + annual_startup + annual_insurance) * disc_factors))
    npv_capex = float(np.sum(annual_capex * disc_factors))
    # Penalties shown in the cost decomposition include the carbon tax (a
    # real cash cost in net_cf) plus the optimizer's operational penalties.
    npv_penalties = float(np.sum((annual_penalties + carbon_cost) * disc_factors))
    npv_tax = float(np.sum(tax * disc_factors))
    npv_tax_benefits = float(
        np.sum((ptc_benefit + itc_by_year) * disc_factors)
    )
    npv_salvage = float(np.sum(salvage * disc_factors))
    npv_total = float(np.sum(net_cf * disc_factors))

    # IRR
    project_irr = _compute_irr(project_cf) if n_years > 1 else 0.0
    equity_irr = _compute_irr(equity_cf) if n_years > 1 else 0.0
    mirr = _compute_mirr(
        project_cf, assumptions.cost_of_debt, assumptions.cost_of_equity,
    )

    # Payback
    payback_s = _payback(project_cf)
    payback_d = _payback(project_cf, discounted=True, rate=discount)

    # Profitability index = PV(future net cash flows) / initial investment
    # = (NPV + PV_capex) / PV_capex.  PI > 1 iff NPV > 0.
    pi = (npv_total + npv_capex) / npv_capex if npv_capex > 0 else 0.0

    # DSCR — CFADS (Cash Flow Available for Debt Service) is the operating
    # cash generated BEFORE debt service and EXCLUDING financed capex:
    #   CFADS = revenue - opex - insurance - carbon - penalties - tax
    #           + ptc + salvage
    # (ITC is a financing-side benefit booked in net_cf and is excluded.)
    cfads_arr = (
        total_revenue - annual_opex - annual_insurance - carbon_cost
        - annual_penalties - tax + ptc_benefit + salvage
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        dscr = np.where(ds > 0, cfads_arr / ds, np.inf)
    dscr_min = float(np.min(dscr[ds > 0])) if np.any(ds > 0) else float("inf")

    # LLCR
    tenor = min(assumptions.debt_tenor, n_years)
    if tenor > 0 and debt_principal > 0:
        llcr = float(np.sum(cfads_arr[:tenor] * disc_factors[:tenor])) / debt_principal
    else:
        llcr = float("inf")

    # System LCOE
    total_gen_discounted = float(np.sum(annual_generation * disc_factors))
    total_cost_discounted = npv_fuel + npv_om + npv_capex
    lcoe_system = (
        total_cost_discounted / total_gen_discounted
        if total_gen_discounted > 0 else float("inf")
    )

    # New-investment (greenfield) metrics — economics of the built capacity
    # only, not inflated by revenue from pre-existing sunk-cost plants.
    inv_metrics = _investment_cashflow_metrics(
        inv_revenue, inv_fuel, inv_om, inv_capex_stream, inv_generation,
        assumptions, discount, n_years,
    )

    # Cash flows DataFrame
    cf_df = pd.DataFrame({
        "year": years,
        "revenue": total_revenue,
        "fuel_cost": annual_fuel,
        "om_cost": annual_om + annual_startup,
        "insurance": annual_insurance,
        "carbon_cost": carbon_cost,
        "penalties": annual_penalties,
        "capex": annual_capex,
        "depreciation": dep_schedule,
        "tax": tax,
        "ptc_benefit": ptc_benefit,
        "itc_benefit": itc_by_year,
        "debt_service": ds,
        "net_cash_flow": net_cf,
        "equity_cash_flow": net_cf - ds,
        "cumulative_npv": np.cumsum(net_cf * disc_factors),
        "dscr": dscr,
    })

    return SystemFinancials(
        npv_capex=npv_capex,
        npv_fuel=npv_fuel,
        npv_om=npv_om,
        npv_decommissioning=0.0,  # requires granular cost extraction
        npv_penalties=npv_penalties,
        npv_revenue=npv_revenue,
        npv_tax=npv_tax,
        npv_tax_benefits=npv_tax_benefits,
        npv_salvage=npv_salvage,
        npv_total=npv_total,
        cash_flows=cf_df,
        project_irr=project_irr,
        equity_irr=equity_irr,
        mirr=mirr,
        payback_simple=payback_s,
        payback_discounted=payback_d,
        wacc=wacc,
        profitability_index=pi,
        dscr_annual=dscr,
        dscr_min=dscr_min,
        llcr=llcr,
        cfads=cfads_arr,
        lcoe_system=lcoe_system,
        investment_npv=inv_metrics["npv"],
        investment_irr=inv_metrics["irr"],
        investment_equity_irr=inv_metrics["equity_irr"],
        investment_payback=inv_metrics["payback"],
        investment_capex=inv_metrics["capex"],
        investment_lcoe=inv_metrics["lcoe"],
    )


def compute_technology_financials(
    h5_path: Path | str,
    assumptions: FinancialAssumptions | None = None,
) -> dict[str, TechnologyFinancials]:
    """Compute per-technology financial breakdown.

    Parameters
    ----------
    h5_path : path to results HDF5 file
    assumptions : financial parameters

    Returns
    -------
    dict mapping technology name to TechnologyFinancials
    """
    if assumptions is None:
        assumptions = FinancialAssumptions()

    h5_path = Path(h5_path)
    info = _load_system_from_h5(h5_path)
    years = info["years"]
    n_years = len(years)
    discount = assumptions.discount_rate if assumptions.discount_rate > 0 else 0.08

    if n_years == 0:
        return {}

    result: dict[str, TechnologyFinancials] = {}

    # Accumulate across years
    tech_acc: dict[str, dict] = {}

    for y_idx, year in enumerate(years):
        scenario_key = info["scenarios"].get(year)
        if not scenario_key:
            continue
        year_data = _load_year_data(h5_path, scenario_key)

        gen_names = sorted(year_data.get("generation", {}).keys())
        nodal_prices = year_data.get("nodal_prices")
        sys_prices = year_data.get("system_prices")
        gen_by_name = _index_configs_by_name(info["gen_configs"])
        bat_by_name = _index_configs_by_name(info["bat_configs"])

        for g_idx, gen_name in enumerate(gen_names):
            gen_arr = year_data["generation"][gen_name]
            cfg = _lookup_config(gen_name, gen_by_name, info["gen_configs"], g_idx)

            if gen_name not in tech_acc:
                tech_acc[gen_name] = {
                    "type": "generator",
                    "fuel": str(cfg.get("fuel_type", cfg.get("type", ""))),
                    "generation": np.zeros(n_years),
                    "revenue": np.zeros(n_years),
                    "fuel_cost": np.zeros(n_years),
                    "om_cost": np.zeros(n_years),
                    "startup_cost": np.zeros(n_years),
                    "capex": 0.0,
                    "installed_mw": 0.0,
                    "rated_power": 0.0,
                }

            acc = tech_acc[gen_name]
            rated = _as_array(cfg.get("rated_power", 0.0), gen_arr.shape[0])
            acc["rated_power"] = max(acc["rated_power"], float(np.max(rated)))

            for n in range(gen_arr.shape[0]):
                output = gen_arr[n, :]
                energy = output * info["temporal_res"]
                acc["generation"][y_idx] += float(np.sum(energy))

                fc = float(_as_array(cfg.get("fuel_cost", 0.0), 1)[0])
                fxc = float(_as_array(cfg.get("fixed_cost", 0.0), 1)[0])
                mc = float(_as_array(cfg.get("maintenance_cost", 0.0), 1)[0])
                acc["fuel_cost"][y_idx] += float(np.sum(energy)) * fc
                acc["om_cost"][y_idx] += float(np.sum(energy)) * (fxc + mc)

                if assumptions.ppa_price > 0:
                    price = assumptions.ppa_price * (
                        1 + assumptions.ppa_escalation
                    ) ** y_idx
                    acc["revenue"][y_idx] += float(np.sum(energy)) * price
                elif nodal_prices is not None and n < nodal_prices.shape[0]:
                    acc["revenue"][y_idx] += float(
                        np.sum(energy * nodal_prices[n, :])
                    )
                elif sys_prices is not None:
                    acc["revenue"][y_idx] += float(np.sum(energy * sys_prices))

            # Investments — at this generator's own technology cost
            inv = year_data.get("gen_investment_power", {}).get(gen_name)
            if inv is not None:
                inv_mw = float(np.sum(inv))
                acc["installed_mw"] += inv_mw
                cost = _resolve_invest_cost(
                    cfg, info["tech_configs"], "invest_cost",
                )
                acc["capex"] += inv_mw * cost

        # Batteries
        for b_idx, bat_name in enumerate(
            sorted(year_data.get("battery_discharge", {}).keys())
        ):
            bat_cfg = _lookup_config(
                bat_name, bat_by_name, info["bat_configs"], b_idx,
            )
            discharge = year_data["battery_discharge"][bat_name]
            charge = year_data.get("battery_charge", {}).get(bat_name)

            if bat_name not in tech_acc:
                tech_acc[bat_name] = {
                    "type": "battery",
                    "fuel": "Storage",
                    "generation": np.zeros(n_years),
                    "revenue": np.zeros(n_years),
                    "fuel_cost": np.zeros(n_years),
                    "om_cost": np.zeros(n_years),
                    "startup_cost": np.zeros(n_years),
                    "capex": 0.0,
                    "installed_mw": 0.0,
                    "rated_power": 0.0,
                    "arbitrage_revenue": np.zeros(n_years),
                    "degradation_cost": np.zeros(n_years),
                }

            acc = tech_acc[bat_name]
            for n in range(discharge.shape[0]):
                dis_energy = discharge[n, :] * info["temporal_res"]
                acc["generation"][y_idx] += float(np.sum(dis_energy))

                # Arbitrage revenue: discharge × price - charge × price
                if nodal_prices is not None and n < nodal_prices.shape[0]:
                    prices_n = nodal_prices[n, :]
                    acc["revenue"][y_idx] += float(np.sum(dis_energy * prices_n))
                    if charge is not None:
                        chg_energy = charge[n, :] * info["temporal_res"]
                        acc["revenue"][y_idx] -= float(
                            np.sum(chg_energy * prices_n)
                        )
                        acc.setdefault("arbitrage_revenue", np.zeros(n_years))
                        acc["arbitrage_revenue"][y_idx] += float(
                            np.sum(dis_energy * prices_n)
                            - np.sum(chg_energy * prices_n)
                        )

                mc = float(_as_array(bat_cfg.get("maintenance_cost", 0.0), 1)[0])
                throughput = dis_energy
                if charge is not None:
                    throughput = throughput + charge[n, :] * info["temporal_res"]
                acc["om_cost"][y_idx] += float(np.sum(throughput)) * mc

    # Build TechnologyFinancials from accumulated data
    disc_factors = np.array([1 / (1 + discount) ** t for t in range(n_years)])

    for name, acc in tech_acc.items():
        total_gen = float(np.sum(acc["generation"]))
        total_rev = float(np.sum(acc["revenue"]))
        total_fuel = float(np.sum(acc["fuel_cost"]))
        total_om = float(np.sum(acc["om_cost"]))
        capex = acc["capex"]

        hours_per_year = 8760
        cf = 0.0
        if acc["rated_power"] > 0 and n_years > 0:
            cf = total_gen / (acc["rated_power"] * hours_per_year * n_years)

        avg_price = total_rev / total_gen if total_gen > 0 else 0.0

        # LCOE
        if total_gen > 0 and capex > 0:
            gen_disc = float(np.sum(acc["generation"] * disc_factors))
            cost_disc = float(np.sum(
                (acc["fuel_cost"] + acc["om_cost"]) * disc_factors
            )) + capex
            lcoe = cost_disc / gen_disc if gen_disc > 0 else float("inf")
        elif total_gen > 0:
            lcoe = (total_fuel + total_om) / total_gen
        else:
            lcoe = float("inf")

        roi = (total_rev - total_fuel - total_om) / capex if capex > 0 else 0.0

        tf = TechnologyFinancials(
            name=name,
            tech_type=acc["type"],
            fuel_type=acc["fuel"],
            capex_total=capex,
            installed_mw=acc["installed_mw"] + acc["rated_power"],
            generation_mwh=total_gen,
            annual_generation=acc["generation"],
            capacity_factor=cf,
            revenue_total=total_rev,
            annual_revenue=acc["revenue"],
            average_selling_price=avg_price,
            fuel_cost_total=total_fuel,
            om_cost_total=total_om,
            lcoe=lcoe,
            roi=roi,
        )

        # Storage-specific
        if acc["type"] == "battery":
            arb = acc.get("arbitrage_revenue", np.zeros(n_years))
            deg = acc.get("degradation_cost", np.zeros(n_years))
            tf.arbitrage_revenue = float(np.sum(arb))
            tf.degradation_cost = float(np.sum(deg))
            if total_gen > 0:
                tf.lcos = (total_om + capex) / total_gen

        result[name] = tf

    return result


def load_price_series(
    h5_path: Path | str, year: int | None = None,
) -> np.ndarray:
    """Return a 1D electricity-price series for one year (base year if None).

    Prefers nodal prices (flattened across nodes × hours), falling back to
    system prices. Used to feed the Market Analysis price-duration curve.
    Returns an empty array if the file has no prices.
    """
    info = _load_system_from_h5(h5_path)
    years = info["years"]
    if not years:
        return np.array([])
    target = year if (year is not None and year in info["scenarios"]) else years[0]
    key = info["scenarios"].get(target)
    if key is None:
        return np.array([])
    data = _load_year_data(h5_path, key)
    nodal = data.get("nodal_prices")
    if nodal is not None:
        return np.asarray(nodal, dtype=float).ravel()
    sys_prices = data.get("system_prices")
    if sys_prices is not None:
        return np.asarray(sys_prices, dtype=float).ravel()
    return np.array([])


# =====================================================================
# Sensitivity & Monte Carlo
# =====================================================================


_SENSITIVITY_VARIABLES = {
    "discount_rate": ("discount_rate", 0.01, 0.20),
    "fuel_cost_factor": None,  # special: multiplier on all fuel costs
    "ppa_price": ("ppa_price", 10.0, 200.0),
    "carbon_price": ("carbon_price", 0.0, 150.0),
    "debt_fraction": ("debt_fraction", 0.0, 0.90),
    "cost_of_debt": ("cost_of_debt", 0.02, 0.15),
    "cost_of_equity": ("cost_of_equity", 0.05, 0.25),
    "tax_rate": ("tax_rate", 0.0, 0.40),
}


# Economic-validity bounds (min, max) for assumption fields. Sweep points
# and Monte Carlo draws are clamped here so a negative discount rate or a
# negative price can never reach compute_system_financials (which would
# blow up discount factors or produce nonsensical NPVs).
_ASSUMPTION_BOUNDS: dict[str, tuple[float, float]] = {
    "discount_rate": (1e-3, 0.40),
    "cost_of_debt": (1e-3, 0.40),
    "cost_of_equity": (1e-3, 0.50),
    "debt_fraction": (0.0, 0.95),
    "tax_rate": (0.0, 0.60),
    "ppa_price": (0.0, 1e4),
    "ppa_escalation": (-0.10, 0.20),
    "carbon_price": (0.0, 1e4),
    "carbon_price_escalation": (-0.10, 0.20),
    "rec_price": (0.0, 1e4),
    "capacity_payment": (0.0, 1e7),
    "insurance_rate": (0.0, 0.10),
    "salvage_fraction": (0.0, 1.0),
    "itc_rate": (0.0, 1.0),
    "ptc_rate": (0.0, 1e3),
}


def _clamp_assumption(var: str, value: float) -> float:
    """Clamp a swept/drawn assumption value to its economic-validity range."""
    bounds = _ASSUMPTION_BOUNDS.get(var)
    if bounds is None:
        return value
    lo, hi = bounds
    return float(min(max(value, lo), hi))


def run_sensitivity_analysis(
    h5_path: Path | str,
    assumptions: FinancialAssumptions,
    variables: list[str] | None = None,
    range_pct: float = 0.30,
    n_points: int = 11,
) -> SensitivityResult:
    """Run one-at-a-time sensitivity analysis.

    Parameters
    ----------
    h5_path : results HDF5 file
    assumptions : base case assumptions
    variables : list of assumption field names to sweep
    range_pct : variation range (0.30 = ±30%)
    n_points : number of sweep points per variable

    Returns
    -------
    SensitivityResult with tornado and sweep data
    """
    if variables is None:
        variables = ["discount_rate", "ppa_price", "carbon_price",
                     "debt_fraction", "cost_of_debt", "tax_rate"]

    from dataclasses import replace

    base = compute_system_financials(h5_path, assumptions)
    result = SensitivityResult(
        base_npv=base.npv_total,
        base_irr=base.project_irr,
    )

    for var in variables:
        base_val = getattr(assumptions, var, None)
        if base_val is None:
            continue

        if base_val == 0:
            lo_val, hi_val = -range_pct, range_pct
        else:
            lo_val = base_val * (1 - range_pct)
            hi_val = base_val * (1 + range_pct)

        sweep_vals = np.linspace(lo_val, hi_val, n_points)
        sweep_results = []

        for sv in sweep_vals:
            clamped = _clamp_assumption(var, float(sv))
            modified = replace(assumptions, **{var: clamped})
            sf = compute_system_financials(h5_path, modified)
            sweep_results.append((clamped, sf.npv_total, sf.project_irr))

        result.sweeps[var] = sweep_results

        # Tornado: NPV at low and high extremes
        npv_low = sweep_results[0][1]
        npv_high = sweep_results[-1][1]
        result.tornado[var] = (npv_low, npv_high)

        # Break-even: find where NPV crosses zero
        for i in range(len(sweep_results) - 1):
            npv_a = sweep_results[i][1]
            npv_b = sweep_results[i + 1][1]
            if npv_a * npv_b < 0:
                val_a = sweep_results[i][0]
                val_b = sweep_results[i + 1][0]
                frac = -npv_a / (npv_b - npv_a) if npv_b != npv_a else 0.5
                result.breakeven[var] = val_a + frac * (val_b - val_a)
                break

    return result


def run_monte_carlo(
    h5_path: Path | str,
    assumptions: FinancialAssumptions,
    distributions: dict[str, tuple[str, float, float]] | None = None,
    n_samples: int = 1000,
    seed: int | None = 42,
) -> MonteCarloResult:
    """Run Monte Carlo simulation on financial metrics.

    Parameters
    ----------
    h5_path : results HDF5 file
    assumptions : base case assumptions
    distributions : {variable: (dist_type, param1, param2)}
        dist_type: "normal" (mean, std), "uniform" (low, high),
                   "triangular" (low, high) with mode at base value
    n_samples : number of Monte Carlo iterations
    seed : random seed for reproducibility

    Returns
    -------
    MonteCarloResult with NPV/IRR distributions and risk metrics
    """
    from dataclasses import replace

    if distributions is None:
        base_dr = assumptions.discount_rate
        base_ppa = max(assumptions.ppa_price, 50.0)
        distributions = {
            "discount_rate": ("normal", base_dr, base_dr * 0.15),
            "ppa_price": ("normal", base_ppa, base_ppa * 0.10),
            "tax_rate": ("uniform", 0.15, 0.35),
        }

    rng = np.random.default_rng(seed)
    npv_samples = np.zeros(n_samples)
    irr_samples = np.zeros(n_samples)

    for i in range(n_samples):
        overrides = {}
        for var, (dist, p1, p2) in distributions.items():
            if dist == "normal":
                draw = float(rng.normal(p1, p2))
            elif dist == "uniform":
                draw = float(rng.uniform(p1, p2))
            elif dist == "triangular":
                base_val = getattr(assumptions, var, (p1 + p2) / 2)
                draw = float(rng.triangular(p1, base_val, p2))
            else:
                continue
            # Clamp to economic-validity bounds so invalid draws (negative
            # rates/prices) never reach the NPV computation.
            overrides[var] = _clamp_assumption(var, draw)

        modified = replace(assumptions, **overrides)
        sf = compute_system_financials(h5_path, modified)
        npv_samples[i] = sf.npv_total
        irr_samples[i] = sf.project_irr

    # Statistics
    npv_sorted = np.sort(npv_samples)
    idx_5 = int(0.05 * n_samples)
    var_5 = float(npv_sorted[idx_5])
    cvar_5 = float(np.mean(npv_sorted[:idx_5])) if idx_5 > 0 else var_5

    return MonteCarloResult(
        n_samples=n_samples,
        npv_samples=npv_samples,
        irr_samples=irr_samples,
        npv_mean=float(np.mean(npv_samples)),
        npv_std=float(np.std(npv_samples)),
        npv_p5=float(np.percentile(npv_samples, 5)),
        npv_p25=float(np.percentile(npv_samples, 25)),
        npv_p50=float(np.percentile(npv_samples, 50)),
        npv_p75=float(np.percentile(npv_samples, 75)),
        npv_p95=float(np.percentile(npv_samples, 95)),
        npv_var_5=var_5,
        npv_cvar_5=cvar_5,
        irr_mean=float(np.mean(irr_samples)),
        irr_std=float(np.std(irr_samples)),
    )
