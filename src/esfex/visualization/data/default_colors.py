"""Default visual colors for map elements.

All colors are delegated to the centralized theme.  This module preserves
the original public API so existing callers keep working.

Note: callers do lazy ``from ... import CONSTANT`` inside methods, so by the
time the import runs the theme singleton is already initialised.
"""

from __future__ import annotations


def _m():
    from esfex.visualization.theme import current_theme
    return current_theme().map_elements


# These are accessed via lazy imports inside form methods, so we use
# a module-level __getattr__ to delegate to the theme at access time.

_ATTR_MAP = {
    "GENERATOR_RENEWABLE": "generator_renewable",
    "GENERATOR_NONRENEWABLE": "generator_nonrenewable",
    "BATTERY": "battery",
    "FUEL_ENTRY": "fuel_entry",
    "TRANSFORMER": "transformer",
    "FUEL_STORAGE": "fuel_storage",
    "ELECTROLYZER": "electrolyzer",
    "ACDC_CONVERTER": "acdc_converter",
    "FREQ_CONVERTER": "freq_converter",
    "BUS": "bus",
    "NODE": "node",
    "TRANSMISSION_LINE": "transmission_line",
    "FUEL_ROUTE": "fuel_route",
    "ZONE": "zone",
}


def __getattr__(name: str):
    attr = _ATTR_MAP.get(name)
    if attr is not None:
        return getattr(_m(), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_generator_color(gen_type: str) -> str:
    """Get default color for a generator based on type."""
    m = _m()
    if gen_type == "Renewable":
        return m.generator_renewable
    return m.generator_nonrenewable
