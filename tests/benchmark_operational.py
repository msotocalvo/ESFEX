#!/usr/bin/env python3
"""Benchmark a single operational window to identify bottlenecks.

Uses Cuba system directly (835 buses, 3068 lines) to measure:
data conversion, model build, solve, result extraction.

Usage:
    python tests/benchmark_operational.py
"""
import sys
import time
import logging
import numpy as np
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("benchmark")


def main():
    from esfex.config.loader import load_config

    config_path = _ROOT / "configs" / "cuba.yaml"
    logger.info(f"Loading config: {config_path}")
    t0 = time.perf_counter()
    config = load_config(str(config_path))
    t_config = time.perf_counter() - t0
    logger.info(f"Config loaded in {t_config:.2f}s")

    # Get Cuba system (the large one)
    # cuba.yaml has IslaJuventud and Cuba — pick Cuba
    for sys_name, sys_cfg in config.systems.items():
        n_buses = len(sys_cfg.buses) if sys_cfg.buses else 0
        n_lines = len(sys_cfg.transmission_lines_geo) if sys_cfg.transmission_lines_geo else 0
        logger.info(f"  System '{sys_name}': {sys_cfg.nodes.num_nodes} nodes, "
                     f"{n_buses} buses, {n_lines} lines")

    # Pick the system with most buses
    sys_name = max(config.systems.keys(),
                   key=lambda k: len(config.systems[k].buses) if config.systems[k].buses else 0)
    sys_config = config.systems[sys_name]
    logger.info(f"Selected: {sys_name}")

    # Parameters
    res_h = config.temporal.resolution_hours  # 6
    window_h = config.temporal.rolling_horizon_hours  # 168
    num_timesteps = window_h // res_h  # 28
    num_nodes = sys_config.nodes.num_nodes
    n_buses = len(sys_config.buses) if sys_config.buses else num_nodes
    n_lines = len(sys_config.transmission_lines_geo) if sys_config.transmission_lines_geo else 0

    # Synthetic demand: 500 MW per node
    demand = np.full((num_timesteps, num_nodes), 500.0)
    logger.info(f"Window: {num_timesteps} timesteps × {num_nodes} nodes "
                f"(res={res_h}h, window={window_h}h)")
    logger.info(f"Network: {n_buses} buses, {n_lines} transmission lines")

    # Initialize Julia
    logger.info("Initializing Julia...")
    t0 = time.perf_counter()
    from esfex.bridge.julia_setup import get_esfex_module, get_julia
    ESFEX = get_esfex_module()
    t_julia = time.perf_counter() - t0
    logger.info(f"Julia initialized in {t_julia:.2f}s")

    # Create PowerSystemAdapter
    from esfex.bridge.adapters import PowerSystemAdapter

    logger.info("=" * 60)
    logger.info(f"BENCHMARKING: {sys_name} ({n_buses} buses, {n_lines} lines)")
    logger.info("=" * 60)

    base_year = sys_config.base_year if hasattr(sys_config, 'base_year') else 2025

    # Force linear losses (instead of PWL) to reduce model size
    if sys_config.dc_power_flow:
        sys_config.dc_power_flow.loss_model = "linear"
        logger.info("Forced loss_model='linear' (no PWL)")

    t_total_start = time.perf_counter()

    # Phase 1: Create adapter
    t0 = time.perf_counter()
    ps = PowerSystemAdapter(
        config=config,
        demand=demand,
        hours=num_timesteps,
        num_nodes=num_nodes,
        year=base_year,
        base_year=base_year,
        mode="economic_dispatch",
        system_config=sys_config,
    )
    t_adapter = time.perf_counter() - t0
    logger.info(f"Phase 1 - Adapter creation: {t_adapter:.2f}s")

    # Phase 2: Build model (includes _create_input + create_power_system)
    t0 = time.perf_counter()
    ps.build_model()
    t_build = time.perf_counter() - t0
    logger.info(f"Phase 2 - build_model() total: {t_build:.2f}s")

    # Phase 3: Solve
    t0 = time.perf_counter()
    status = ps.solve()
    t_solve = time.perf_counter() - t0
    logger.info(f"Phase 3 - solve(): {t_solve:.2f}s (status={status})")

    # Phase 4: Extract results
    if status == 1:
        t0 = time.perf_counter()
        solution = ps.get_solution_values()
        t_extract = time.perf_counter() - t0
        logger.info(f"Phase 4 - get_solution_values(): {t_extract:.2f}s")
        obj = solution.get('objective', 0.0)
        logger.info(f"Objective: ${obj:,.2f}")
    else:
        t_extract = 0.0
        logger.warning("Solve failed, skipping result extraction")

    t_total = time.perf_counter() - t_total_start

    logger.info("=" * 60)
    logger.info("TIMING SUMMARY")
    logger.info("=" * 60)
    logger.info(f"  Adapter creation:    {t_adapter:8.2f}s")
    logger.info(f"  build_model() total: {t_build:8.2f}s")
    logger.info(f"  solve():             {t_solve:8.2f}s")
    logger.info(f"  get_solution_values: {t_extract:8.2f}s")
    logger.info(f"  ─────────────────────────────")
    logger.info(f"  TOTAL:               {t_total:8.2f}s")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
