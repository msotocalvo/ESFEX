"""Network topology reduction for DCOPF performance.

This module implements mathematically-equivalent internal network reduction.
The original topology is preserved via a :class:`ReductionMap` so that LP
results (flows, angles, prices) can be expanded back to every original bus
and line after solving.

Public API
----------
- :func:`reduce_network` — produce a reduced :class:`SystemConfig` and
  the corresponding :class:`ReductionMap`.
- :class:`ReductionMap` — serializable reversible mapping.
- :class:`ResultExpander` — post-solve expansion of LP results to the
  original topology.

Techniques (Phase 1: local, algebraic)
--------------------------------------
- **Leaf pruning**: degree-1 passive buses (no equipment, no demand) are
  removed together with their single line; the bus inherits its neighbour's
  voltage angle and its line carries zero flow.
- **Parallel merge**: multiple lines between the same bus pair are merged
  into a single equivalent line (admittance sum, capacity sum). Original
  flows are recovered by the admittance ratio.
- **Series collapse**: a degree-2 passive bus is eliminated by merging the
  two adjacent lines into one (reactance and resistance summed, capacity =
  bottleneck). The eliminated bus's angle is recovered by linear
  interpolation.

All transformations preserve DCOPF power flow **exactly** for the linear
formulation; they are not approximations.
"""

from esfex.topology.network_reducer import reduce_network
from esfex.topology.reduction_map import (
    MergedLine,
    OriginalLineRef,
    PrunedBus,
    ReductionMap,
)
from esfex.topology.result_expander import ResultExpander

__all__ = [
    "reduce_network",
    "ReductionMap",
    "MergedLine",
    "OriginalLineRef",
    "PrunedBus",
    "ResultExpander",
]
