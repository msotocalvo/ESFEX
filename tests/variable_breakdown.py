#!/usr/bin/env python3
"""Quick diagnostic: build ONE operational window and log variable breakdown.

Uses the full merged system (same as real simulation) but only builds, doesn't solve.
"""
import sys
import time
import logging
import numpy as np
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("varcount")


def main():
    from esfex.config.loader import load_config

    config_path = _ROOT / "configs" / "cuba.yaml"
    logger.info(f"Loading config: {config_path}")
    config = load_config(str(config_path))

    # Merge systems (same as runner does)
    from esfex.runner import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.config = config
    _, sys_config, _ = orch._merge_systems(config)
    num_nodes = sys_config.nodes.num_nodes
    n_buses = len(sys_config.buses) if sys_config.buses else num_nodes
    n_lines = len(sys_config.transmission_lines_geo) if sys_config.transmission_lines_geo else 0

    res_h = config.temporal.resolution_hours
    window_h = config.temporal.rolling_horizon_hours
    num_timesteps = window_h // res_h

    logger.info(f"Merged system: {num_nodes} nodes, {n_buses} buses, {n_lines} lines")
    logger.info(f"Generators: {len(sys_config.generators)}, Batteries: {len(sys_config.batteries)}")
    logger.info(f"Window: {num_timesteps} timesteps (res={res_h}h, window={window_h}h)")

    # Synthetic demand
    demand = np.full((num_timesteps, num_nodes), 500.0)

    # Initialize Julia
    logger.info("Initializing Julia...")
    from esfex.bridge.julia_setup import get_esfex_module
    ESFEX = get_esfex_module()

    from esfex.bridge.adapters import PowerSystemAdapter

    base_year = sys_config.base_year if hasattr(sys_config, 'base_year') else 2025

    # Use development mode (same as real simulation)
    ps = PowerSystemAdapter(
        config=config,
        demand=demand,
        hours=num_timesteps,
        num_nodes=num_nodes,
        year=base_year,
        base_year=base_year,
        mode="development",
        system_config=sys_config,
    )

    logger.info("Building model (build only, no solve)...")
    t0 = time.perf_counter()
    ps.build_model()
    t_build = time.perf_counter() - t0
    logger.info(f"Build completed in {t_build:.2f}s")
    logger.info("Check Julia @info output above for variable breakdown.")


if __name__ == "__main__":
    main()
