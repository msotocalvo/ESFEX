"""
Technology adoption models for rooftop solar PV.

Provides four modeling approaches:
1. Logistic regression — macro-economic drivers
2. Bass diffusion — innovation/imitation dynamics
3. Techno-economic — LCOE vs electricity tariff
4. Agent-based — heterogeneous household decisions

Each method returns an ``AdoptionCurve`` with year-by-year penetration
and installed capacity projections.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# Data structures
# ══════════════════════════════════════════════════════════════════


@dataclass
class MacroeconomicData:
    """Macroeconomic inputs for adoption modeling."""

    country_iso: str = ""
    gdp_per_capita: float = 5000.0          # USD
    electricity_tariff: float = 0.15        # $/kWh
    pv_system_cost: float = 1200.0          # $/kW (current year)
    pv_cost_learning_rate: float = 0.20     # cost reduction per capacity doubling
    urbanization_pct: float = 75.0          # %
    population: int = 1_000_000
    discount_rate: float = 0.08
    inflation_rate: float = 0.03
    gdp_growth_rate: float = 0.03
    # Year → $/kW cost trajectory (optional, for techno-economic)
    pv_cost_trajectory: dict[int, float] = field(default_factory=dict)


@dataclass
class AdoptionCurve:
    """Result of an adoption model run."""

    method: str                     # "logistic", "bass", "techno_economic", "abm"
    years: list[int] = field(default_factory=list)
    penetration: list[float] = field(default_factory=list)    # fraction [0..1]
    capacity_mw: list[float] = field(default_factory=list)    # MW installed
    confidence_low: list[float] = field(default_factory=list)  # lower bound
    confidence_high: list[float] = field(default_factory=list) # upper bound
    parameters: dict = field(default_factory=dict)


@dataclass
class ValidationData:
    """Observed/historical data for adoption model validation."""

    label: str                    # e.g. "IRENA Observed", "User Data"
    years: list[int] = field(default_factory=list)
    capacity_mw: list[float] = field(default_factory=list)  # MW installed
    source: str = "manual"        # "irena", "user_csv", "manual"


# ══════════════════════════════════════════════════════════════════
# Method 1: Logistic Regression
# ══════════════════════════════════════════════════════════════════


def run_logistic_adoption(
    macro: MacroeconomicData,
    max_potential_mw: float,
    base_year: int = 2025,
    target_year: int = 2050,
    coefficients: Optional[dict] = None,
) -> AdoptionCurve:
    """Logistic regression adoption model driven by macro-economic variables.

    The adoption probability evolves as GDP grows, PV costs decline,
    and electricity tariffs change over time.

    Parameters
    ----------
    macro : MacroeconomicData
        Current macroeconomic parameters.
    max_potential_mw : float
        Technical maximum rooftop PV capacity (MW).
    coefficients : dict, optional
        Regression coefficients: ``beta_0`` (intercept), ``beta_gdp``,
        ``beta_tariff``, ``beta_cost``, ``beta_urban``, ``beta_policy``.
    """
    coeff = {
        "beta_0": -3.0,
        "beta_gdp": 0.00005,       # higher GDP → more adoption
        "beta_tariff": 8.0,        # higher tariff → more adoption
        "beta_cost": -0.001,       # higher PV cost → less adoption
        "beta_urban": 0.02,        # higher urbanization → more adoption
        "beta_policy": 0.5,        # policy incentive factor [0..1]
    }
    if coefficients:
        coeff.update(coefficients)

    policy_factor = coeff.get("beta_policy", 0.5)

    years = list(range(base_year, target_year + 1))
    penetration = []
    capacity_mw = []

    for yr in years:
        t = yr - base_year
        # Project macro variables forward
        gdp = macro.gdp_per_capita * (1 + macro.gdp_growth_rate) ** t
        tariff = macro.electricity_tariff * (1 + macro.inflation_rate * 0.5) ** t
        # PV cost declines with learning curve (assume 15% annual deployment growth)
        cost = macro.pv_system_cost * (1 - 0.04) ** t  # ~4% annual decline
        if yr in macro.pv_cost_trajectory:
            cost = macro.pv_cost_trajectory[yr]
        urban = macro.urbanization_pct

        z = (
            coeff["beta_0"]
            + coeff["beta_gdp"] * gdp
            + coeff["beta_tariff"] * tariff
            + coeff["beta_cost"] * cost
            + coeff["beta_urban"] * urban
            + policy_factor
        )
        prob = 1.0 / (1.0 + math.exp(-z))
        penetration.append(prob)
        capacity_mw.append(prob * max_potential_mw)

    return AdoptionCurve(
        method="logistic",
        years=years,
        penetration=penetration,
        capacity_mw=capacity_mw,
        parameters=coeff,
    )


# ══════════════════════════════════════════════════════════════════
# Method 2: Bass Diffusion
# ══════════════════════════════════════════════════════════════════


def run_bass_diffusion(
    max_potential_mw: float,
    base_year: int = 2025,
    target_year: int = 2050,
    p: float = 0.03,
    q: float = 0.38,
    initial_penetration: float = 0.01,
) -> AdoptionCurve:
    """Bass diffusion model: innovation (p) + imitation (q).

    F(t) = (1 - exp(-(p+q)t)) / (1 + (q/p)exp(-(p+q)t))

    Parameters
    ----------
    p : float
        Innovation coefficient (external influence).
    q : float
        Imitation coefficient (internal/word-of-mouth influence).
    initial_penetration : float
        Fraction already adopted at base_year.
    """
    years = list(range(base_year, target_year + 1))
    penetration = []
    capacity_mw = []

    # Find t_offset such that F(t_offset) = initial_penetration
    # F(t) = (1 - exp(-(p+q)t)) / (1 + (q/p)*exp(-(p+q)t))
    # Solve numerically via bisection
    t_offset = 0.0
    if initial_penetration > 0.001:
        for t_try in np.linspace(0, 50, 500):
            exp_val = math.exp(-(p + q) * t_try)
            f_val = (1.0 - exp_val) / (1.0 + (q / max(p, 1e-9)) * exp_val)
            if f_val >= initial_penetration:
                t_offset = t_try
                break

    for yr in years:
        t = (yr - base_year) + t_offset
        exp_val = math.exp(-(p + q) * t)
        f_val = (1.0 - exp_val) / (1.0 + (q / max(p, 1e-9)) * exp_val)
        f_val = max(0.0, min(1.0, f_val))
        penetration.append(f_val)
        capacity_mw.append(f_val * max_potential_mw)

    return AdoptionCurve(
        method="bass",
        years=years,
        penetration=penetration,
        capacity_mw=capacity_mw,
        parameters={"p": p, "q": q, "initial_penetration": initial_penetration},
    )


# ══════════════════════════════════════════════════════════════════
# Method 3: Techno-Economic (LCOE vs Tariff)
# ══════════════════════════════════════════════════════════════════


def run_techno_economic(
    macro: MacroeconomicData,
    max_potential_mw: float,
    avg_irradiance_kwh_m2: float = 1600.0,
    base_year: int = 2025,
    target_year: int = 2050,
    system_lifetime: int = 25,
    performance_ratio: float = 0.80,
    degradation_rate: float = 0.005,
    price_sensitivity: float = 15.0,
) -> AdoptionCurve:
    """Techno-economic adoption: LCOE vs electricity tariff.

    Computes LCOE of rooftop PV each year as costs decline.
    Adoption follows a sigmoid function of the gap (tariff - LCOE).

    Parameters
    ----------
    avg_irradiance_kwh_m2 : float
        Annual Global Horizontal Irradiance (kWh/m²/year).
    price_sensitivity : float
        Steepness of the sigmoid response to tariff-LCOE gap.
    """
    r = macro.discount_rate
    n = system_lifetime

    # Capital Recovery Factor
    if r > 0:
        crf = r * (1 + r) ** n / ((1 + r) ** n - 1)
    else:
        crf = 1.0 / n

    years = list(range(base_year, target_year + 1))
    penetration = []
    capacity_mw = []

    # Capacity factor from irradiance
    # CF = irradiance * PR / 8760 (kWh/kW/year → fraction)
    specific_yield = avg_irradiance_kwh_m2 * performance_ratio  # kWh/kWp/year
    cf = specific_yield / 8760.0

    for yr in years:
        t = yr - base_year

        # PV system cost projection
        if yr in macro.pv_cost_trajectory:
            cost = macro.pv_cost_trajectory[yr]
        else:
            cost = macro.pv_system_cost * (1 - 0.04) ** t

        # Account for degradation in average lifetime yield
        avg_degradation = 1.0 - degradation_rate * n / 2.0

        # LCOE ($/kWh) = (cost * CRF) / (CF * 8760 * avg_degradation)
        annual_yield = cf * 8760.0 * avg_degradation  # kWh/kW/year (degradation-adjusted)
        lcoe = (cost * crf) / max(annual_yield, 1.0)

        # Electricity tariff projection
        tariff = macro.electricity_tariff * (1 + macro.inflation_rate * 0.5) ** t

        # Sigmoid adoption based on tariff - LCOE gap
        gap = tariff - lcoe
        prob = 1.0 / (1.0 + math.exp(-price_sensitivity * gap))
        prob = max(0.0, min(1.0, prob))
        penetration.append(prob)
        capacity_mw.append(prob * max_potential_mw)

    return AdoptionCurve(
        method="techno_economic",
        years=years,
        penetration=penetration,
        capacity_mw=capacity_mw,
        parameters={
            "system_lifetime": system_lifetime,
            "performance_ratio": performance_ratio,
            "degradation_rate": degradation_rate,
            "price_sensitivity": price_sensitivity,
            "avg_irradiance": avg_irradiance_kwh_m2,
        },
    )


# ══════════════════════════════════════════════════════════════════
# Method 4: Agent-Based Model
# ══════════════════════════════════════════════════════════════════


def run_abm_adoption(
    macro: MacroeconomicData,
    max_potential_mw: float,
    base_year: int = 2025,
    target_year: int = 2050,
    n_agents: int = 1000,
    n_iterations: int = 20,
    building_positions: Optional[np.ndarray] = None,
    neighbor_radius_km: float = 1.0,
    income_std_factor: float = 0.5,
    w_economic: float = 0.5,
    w_social: float = 0.3,
    w_awareness: float = 0.2,
    adoption_threshold: float = 0.5,
    system_lifetime: int = 25,
    performance_ratio: float = 0.80,
    avg_irradiance_kwh_m2: float = 1600.0,
    seed: Optional[int] = None,
) -> AdoptionCurve:
    """Agent-based adoption model with heterogeneous households.

    Each agent evaluates adoption based on weighted utility:
    ``utility = w_economic * economic + w_social * social + w_awareness * awareness``

    Parameters
    ----------
    n_agents : int
        Number of household agents to simulate.
    n_iterations : int
        Number of stochastic iterations for confidence bounds.
    building_positions : np.ndarray, optional
        (N, 2) array of (lat, lon) for spatial neighbor effects.
        If None, agents are placed randomly.
    neighbor_radius_km : float
        Radius for counting adopted neighbors (peer effect).
    income_std_factor : float
        Standard deviation of income as fraction of GDP/capita.
    w_economic, w_social, w_awareness : float
        Weights for utility components (should sum to 1).
    adoption_threshold : float
        Minimum utility to trigger adoption [0..1].
    """
    rng = np.random.default_rng(seed)
    years = list(range(base_year, target_year + 1))
    n_years = len(years)

    # Generate agent properties
    incomes = rng.normal(
        macro.gdp_per_capita,
        macro.gdp_per_capita * income_std_factor,
        n_agents,
    )
    incomes = np.clip(incomes, macro.gdp_per_capita * 0.1, macro.gdp_per_capita * 5.0)

    # Personal discount rates (lower income → higher discount rate)
    personal_dr = macro.discount_rate * (macro.gdp_per_capita / np.maximum(incomes, 1))
    personal_dr = np.clip(personal_dr, 0.02, 0.30)

    # Agent positions for neighbor effects
    if building_positions is not None and len(building_positions) >= n_agents:
        positions = building_positions[:n_agents]
    elif building_positions is not None and len(building_positions) > 0:
        indices = rng.choice(len(building_positions), n_agents, replace=True)
        positions = building_positions[indices]
    else:
        positions = rng.uniform(0, 10, (n_agents, 2))  # arbitrary grid

    # Pre-compute neighbor graph (indices within radius)
    # Use simple distance matrix for moderate agent counts
    from scipy.spatial import cKDTree

    tree = cKDTree(positions)
    # Convert km to approximate degree distance (~0.009 deg/km)
    radius_deg = neighbor_radius_km * 0.009
    neighbor_lists = tree.query_ball_tree(tree, radius_deg)

    # Specific yield for NPV calculation
    specific_yield = avg_irradiance_kwh_m2 * performance_ratio  # kWh/kWp/year

    # Run multiple iterations
    all_penetrations = np.zeros((n_iterations, n_years))

    for iteration in range(n_iterations):
        adopted = np.zeros(n_agents, dtype=bool)
        iter_rng = np.random.default_rng(seed + iteration if seed else None)

        for yi, yr in enumerate(years):
            t = yr - base_year

            # PV cost this year
            if yr in macro.pv_cost_trajectory:
                cost = macro.pv_cost_trajectory[yr]
            else:
                cost = macro.pv_system_cost * (1 - 0.04) ** t

            # Tariff this year
            tariff = macro.electricity_tariff * (1 + macro.inflation_rate * 0.5) ** t

            # Awareness grows logistically
            awareness_base = 1.0 / (1.0 + math.exp(-0.3 * (t - 10)))

            for agent in range(n_agents):
                if adopted[agent]:
                    continue

                # 1. Economic utility: NPV of savings
                r_a = personal_dr[agent]
                n_life = system_lifetime
                if r_a > 0:
                    crf_a = r_a * (1 + r_a) ** n_life / ((1 + r_a) ** n_life - 1)
                else:
                    crf_a = 1.0 / n_life
                lcoe_a = (cost * crf_a) / max(specific_yield, 1.0)
                econ = 1.0 / (1.0 + math.exp(-10 * (tariff - lcoe_a)))

                # 2. Social utility: fraction of neighbors adopted
                neighbors = neighbor_lists[agent]
                if len(neighbors) > 1:
                    n_adopted = sum(1 for nb in neighbors if adopted[nb])
                    social = n_adopted / len(neighbors)
                else:
                    social = 0.0

                # 3. Awareness (with personal noise)
                awareness = awareness_base + iter_rng.normal(0, 0.1)
                awareness = max(0.0, min(1.0, awareness))

                # Combined utility
                utility = (
                    w_economic * econ
                    + w_social * social
                    + w_awareness * awareness
                )

                # Adopt if utility exceeds threshold (with noise)
                threshold = adoption_threshold + iter_rng.normal(0, 0.05)
                if utility > threshold:
                    adopted[agent] = True

            all_penetrations[iteration, yi] = adopted.sum() / n_agents

    # Aggregate across iterations
    mean_pen = np.mean(all_penetrations, axis=0)
    low_pen = np.percentile(all_penetrations, 10, axis=0)
    high_pen = np.percentile(all_penetrations, 90, axis=0)

    return AdoptionCurve(
        method="abm",
        years=years,
        penetration=mean_pen.tolist(),
        capacity_mw=(mean_pen * max_potential_mw).tolist(),
        confidence_low=low_pen.tolist(),
        confidence_high=high_pen.tolist(),
        parameters={
            "n_agents": n_agents,
            "n_iterations": n_iterations,
            "neighbor_radius_km": neighbor_radius_km,
            "w_economic": w_economic,
            "w_social": w_social,
            "w_awareness": w_awareness,
            "adoption_threshold": adoption_threshold,
        },
    )


# ══════════════════════════════════════════════════════════════════
# Integration helper
# ══════════════════════════════════════════════════════════════════


def fit_adoption_to_rooftop_config(
    curve: AdoptionCurve,
    macro: MacroeconomicData,
    num_nodes: int,
    systems_per_node: list[int],
    avg_system_size: list[float],
    performance_ratio: float = 0.80,
    degradation_rate: float = 0.005,
) -> dict:
    """Convert an AdoptionCurve into parameters for GuiRooftopSolar.

    Returns a dict suitable for updating ``GuiRooftopSolar`` fields.
    """
    if not curve.years or not curve.penetration:
        return {}

    base_year = curve.years[0]
    target_year = curve.years[-1]

    # Initial adoption (at base year)
    initial_adoption = [curve.penetration[0]] * num_nodes

    # Fit max adoption from saturation (last value)
    max_pen = curve.penetration[-1]
    max_adoption = {
        "low": max_pen * 0.6,
        "medium": max_pen,
        "high": min(0.95, max_pen * 1.3),
    }

    # Estimate adoption rate from curve midpoint slope
    mid_idx = len(curve.penetration) // 2
    if mid_idx > 0 and mid_idx < len(curve.penetration) - 1:
        slope = (curve.penetration[mid_idx + 1] - curve.penetration[mid_idx - 1]) / 2.0
        rate = max(0.01, min(0.25, slope * 2))
    else:
        rate = 0.08
    adoption_rates = {
        "low": rate * 0.6,
        "medium": rate,
        "high": rate * 1.5,
    }

    return {
        "adoption_scenario": "medium",
        "base_year": base_year,
        "target_year": target_year,
        "systems_per_node": systems_per_node,
        "avg_system_size": avg_system_size,
        "initial_adoption": initial_adoption,
        "max_adoption": max_adoption,
        "adoption_rates": adoption_rates,
        "cost_per_kw": macro.pv_system_cost,
        "performance_ratio": performance_ratio,
        "degradation_rate": degradation_rate,
    }
