"""
ESFEX: Energy System FlEXibility — Power System Optimization

A hybrid Python/Julia optimization framework for power system planning and operation.
"""

# Single source of truth: the version comes from the installed package
# metadata (i.e. pyproject.toml at build time), so the splash screen, the
# About dialog and ``esfex.__version__`` always track the released version
# without a hand-maintained literal here.
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("esfex")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+source"
__author__ = "Manuel Soto Calvo & Han Soo Lee"

from esfex.config.loader import load_config
from esfex.config.schema import ESFEXConfig

__all__ = [
    "__version__",
    "load_config",
    "ESFEXConfig",
]
