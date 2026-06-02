"""Power system dynamic analysis modules.

This package provides post-optimization analysis tools for frequency
stability, N-1 contingency assessment, and related calculations.
"""

from esfex.analysis.ac_types import ACPowerFlowResult, ShortCircuitResult
from esfex.analysis.frequency import (
    FrequencyAnalyzer,
    FrequencyResponse,
    build_gen_freq_params_from_state,
)
from esfex.analysis.contingency import ContingencyAnalyzer, ContingencyResult
from esfex.analysis.ac_contingency import ACContingencyAnalyzer, ACContingencyResult
from esfex.analysis.n1_assessment import IntegratedN1Analyzer, N1SecurityAssessment

__all__ = [
    "ACPowerFlowResult",
    "ShortCircuitResult",
    "FrequencyAnalyzer",
    "FrequencyResponse",
    "build_gen_freq_params_from_state",
    "ContingencyAnalyzer",
    "ContingencyResult",
    "ACContingencyAnalyzer",
    "ACContingencyResult",
    "IntegratedN1Analyzer",
    "N1SecurityAssessment",
]

# Conditional pandapower exports
try:
    from esfex.analysis.pandapower_bridge import PandapowerBridge

    __all__ += ["PandapowerBridge"]
except ImportError:
    pass

# Native AC bridge (always available — no external dependencies)
try:
    from esfex.analysis.native_ac_bridge import NativeACBridge

    __all__ += ["NativeACBridge"]
except ImportError:
    pass
