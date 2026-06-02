"""Voltage-level to color mapping for single-line diagram bus bars.

Colors follow common power system conventions (similar to IEC/PowerFactory):
higher voltages use warmer colors, lower voltages use cooler colors.
"""

from __future__ import annotations

# Standard voltage levels (kV) → color mapping
VOLTAGE_COLORS: dict[float, str] = {
    765: "#FF0000",   # red
    500: "#E74C3C",   # dark red
    400: "#C0392B",   # crimson
    345: "#9B59B6",   # purple
    230: "#2980B9",   # blue
    220: "#3498DB",   # light blue
    138: "#27AE60",   # green
    115: "#2ECC71",   # light green
    110: "#16A085",   # teal
    69:  "#F39C12",   # orange
    66:  "#F39C12",   # orange
    33:  "#D35400",   # dark orange
    22:  "#D35400",   # dark orange
    11:  "#7F8C8D",   # gray
    0.4: "#95A5A6",   # light gray (LV)
}

# Default color when voltage doesn't match any standard level
DEFAULT_BUS_COLOR = "#34495E"


def get_voltage_color(kv: float) -> str:
    """Return color for the nearest standard voltage level.

    Finds the closest standard voltage in VOLTAGE_COLORS and returns
    its color.  Falls back to DEFAULT_BUS_COLOR for very unusual values.
    """
    if not VOLTAGE_COLORS:
        return DEFAULT_BUS_COLOR

    best_kv = min(VOLTAGE_COLORS.keys(), key=lambda v: abs(v - kv))
    # Only match if within 20% of a standard voltage
    if best_kv > 0 and abs(best_kv - kv) / best_kv > 0.20:
        return DEFAULT_BUS_COLOR
    return VOLTAGE_COLORS[best_kv]


def get_voltage_layer_priority(kv: float) -> int:
    """Return a layer priority for ELK layout (lower = higher in diagram).

    Higher voltage buses appear at the top of the single-line diagram.
    """
    if kv >= 400:
        return 0
    if kv >= 200:
        return 1
    if kv >= 100:
        return 2
    if kv >= 50:
        return 3
    return 4
