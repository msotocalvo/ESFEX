"""Shared result types for AC power flow and short-circuit analysis.

These dataclasses are backend-agnostic — used by both the native Julia
Newton-Raphson solver (``NativeACBridge``) and the pandapower bridge
(``PandapowerBridge``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ACPowerFlowResult:
    """Results from an AC Newton-Raphson power flow."""

    converged: bool = False
    iterations: int = 0

    # Per bus: bus_id → values
    bus_vm_pu: dict[str, float] = field(default_factory=dict)
    bus_va_deg: dict[str, float] = field(default_factory=dict)
    bus_p_mw: dict[str, float] = field(default_factory=dict)
    bus_q_mvar: dict[str, float] = field(default_factory=dict)

    # Per line: edge_id → values
    line_p_from_mw: dict[str, float] = field(default_factory=dict)
    line_q_from_mvar: dict[str, float] = field(default_factory=dict)
    line_p_loss_mw: dict[str, float] = field(default_factory=dict)
    line_loading_pct: dict[str, float] = field(default_factory=dict)

    # Per generator: gen_id → values
    gen_p_mw: dict[str, float] = field(default_factory=dict)
    gen_q_mvar: dict[str, float] = field(default_factory=dict)

    # Summary
    total_losses_mw: float = 0.0
    voltage_violations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ShortCircuitResult:
    """Results from IEC 60909 short-circuit analysis."""

    # Per bus: bus_id → values
    ik_ka: dict[str, float] = field(default_factory=dict)   # Initial SC current
    ip_ka: dict[str, float] = field(default_factory=dict)   # Peak SC current
    sk_mva: dict[str, float] = field(default_factory=dict)  # SC power
