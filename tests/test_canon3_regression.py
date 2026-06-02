"""Canonical 3-node regression test.

`canon3` is a deliberately small synthetic system: 3 nodes x 4 buses, a single
3 GW firm gas plant at node 0, and demand at nodes 1 and 2 that can only be
served by routing power across the inter-node lines and the GSU/StepDown
transformers. Because all generation is concentrated behind transformers and
must traverse the network, this configuration is acutely sensitive to the
operational DC-OPF being physically correct.

It was built while root-causing a multi-week "massive load shedding" bug and
independently exposed three distinct model defects, each of which forced
>=50-100% spurious load shed here:

  1. PWL transmission loss coefficient used the AC admittance conductance
     `G = R/(R^2+X^2)` instead of the per-unit series resistance `R_pu`,
     overstating losses ~400x and making power routing infeasible.
  2. The DC voltage-angle-difference limit (with the capped `b_line`) imposed
     a non-physical `pf <= b_line*max_angle` throttle far below thermal rating.
  3. N-1 generation security was a HARD constraint, making a fixed inadequate
     fleet INFEASIBLE with no recourse.

With a correct model, canon3 serves essentially all demand (~0% shed). This
test asserts that property so any regression that re-introduces gross
shedding / routing-infeasibility is caught in CI.
"""

import glob
from pathlib import Path

import numpy as np
import pytest

from esfex.config.loader import load_config
from esfex.runner import Orchestrator

pytestmark = pytest.mark.julia

FIXTURE = Path("tests/fixtures/canon3_regression.yaml")

# canon3 with a correct model shees ~0%. The three historical bugs each caused
# >=50% shed, so a 1% ceiling robustly catches the regression class while
# tolerating negligible numerical shedding.
MAX_SHED_FRACTION = 0.01


def test_canon3_serves_demand(tmp_path):
    """canon3 must solve and serve ~all demand (no gross spurious shedding)."""
    cfg = load_config(FIXTURE)
    orchestrator = Orchestrator(cfg, output_dir=tmp_path, config_path=FIXTURE)
    # 2 years is enough to exercise master + operational and catch the
    # routing/loss/N-1 regression class without a full 25-year run.
    orchestrator.run(years=2)

    h5_files = sorted(glob.glob(str(tmp_path / "*.h5")))
    assert h5_files, "no results .h5 produced"

    import h5py

    with h5py.File(h5_files[0], "r") as f:
        dr = f.get("detailed_results")
        assert dr is not None and len(dr.keys()) > 0, "no detailed_results in output"

        total_demand = 0.0
        total_shed = 0.0
        for year_key in dr.keys():
            grp = dr[year_key]
            demand = np.asarray(grp["demand"][()], dtype=float)
            shed = np.asarray(grp["loss_load"][()], dtype=float)
            total_demand += float(np.nansum(demand))
            total_shed += float(np.nansum(shed))

    assert total_demand > 0, "fixture produced zero demand"
    shed_fraction = total_shed / total_demand
    assert shed_fraction < MAX_SHED_FRACTION, (
        f"canon3 shed {shed_fraction:.1%} of demand "
        f"(>{MAX_SHED_FRACTION:.0%} ceiling) — likely a regression in the "
        f"operational DC-OPF (loss coefficient / angle limits / N-1)."
    )
