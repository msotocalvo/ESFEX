"""
ESFEX: Energy System FlEXibility — Power System Optimization

A hybrid Python/Julia optimization framework for power system planning and operation.
"""

__version__ = "0.1.0"
__author__ = "Manuel Soto Calvo & Han Soo Lee"

from esfex.config.loader import load_config
from esfex.config.schema import ESFEXConfig

__all__ = [
    "__version__",
    "load_config",
    "ESFEXConfig",
]
