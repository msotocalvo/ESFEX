"""Utility helpers for multi-element editing with mixed-value display."""

from __future__ import annotations

from typing import Any, Sequence

from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox


_MIXED = object()  # Sentinel indicating values differ across elements


def collect_attr(instances: Sequence, attr: str) -> Any:
    """Return the common value if all instances share it, else ``_MIXED``."""
    vals: set = set()
    for inst in instances:
        v = getattr(inst, attr, None)
        # Convert unhashable types
        if isinstance(v, list):
            v = tuple(v)
        elif isinstance(v, dict):
            v = tuple(sorted(v.items()))
        vals.add(v)
    if len(vals) == 1:
        v = vals.pop()
        # Convert back
        if isinstance(v, tuple):
            # Check if it was a dict
            try:
                return dict(v)
            except (TypeError, ValueError):
                return list(v)
        return v
    return _MIXED


def is_mixed(val: Any) -> bool:
    """Check if a value is the _MIXED sentinel."""
    return val is _MIXED


# ── Widget setters ────────────────────────────────────────────────


def set_widget_value(widget, val: Any) -> None:
    """Set *widget* to *val*, or to the mixed state if ``val is _MIXED``."""
    if val is _MIXED:
        set_mixed(widget)
        return

    if isinstance(widget, QLineEdit):
        widget.setPlaceholderText("")
        widget.setText(val if isinstance(val, str) else str(val))
    elif isinstance(widget, (QDoubleSpinBox, QSpinBox)):
        _clear_mixed_spinbox(widget)
        if val is not None:
            widget.setValue(val)
        else:
            widget.setValue(widget.minimum())
    elif isinstance(widget, QComboBox):
        _clear_mixed_combo(widget)
        if isinstance(val, int):
            idx = widget.findData(val)
            if idx < 0:
                idx = widget.findText(str(val))
        else:
            idx = widget.findText(str(val))
        if idx >= 0:
            widget.setCurrentIndex(idx)


def set_mixed(widget) -> None:
    """Put *widget* into 'mixed values' display state."""
    if isinstance(widget, QLineEdit):
        widget.clear()
        widget.setPlaceholderText("-- mixed values --")
    elif isinstance(widget, (QDoubleSpinBox, QSpinBox)):
        if widget.property("_real_min") is None:
            widget.setProperty("_real_min", widget.minimum())
        widget.setSpecialValueText("-- mixed --")
        real_min = widget.property("_real_min")
        widget.setMinimum(real_min - 1)
        widget.setValue(widget.minimum())
    elif isinstance(widget, QComboBox):
        if not widget.property("_has_mixed"):
            widget.blockSignals(True)
            widget.insertItem(0, "-- mixed --")
            widget.setProperty("_has_mixed", True)
            widget.blockSignals(False)
        widget.blockSignals(True)
        widget.setCurrentIndex(0)
        widget.blockSignals(False)


def _clear_mixed_spinbox(sb) -> None:
    """Restore spinbox from mixed state if needed."""
    real_min = sb.property("_real_min")
    if real_min is not None:
        sb.setMinimum(real_min)
        sb.setProperty("_real_min", None)
        sb.setSpecialValueText("")


def _clear_mixed_combo(cb) -> None:
    """Remove the '-- mixed --' placeholder item from a combo box."""
    if cb.property("_has_mixed"):
        cb.blockSignals(True)
        cb.removeItem(0)
        cb.setProperty("_has_mixed", False)
        cb.blockSignals(False)


# ── Widget query ──────────────────────────────────────────────────


def widget_is_mixed(widget) -> bool:
    """Return True if *widget* is currently showing the mixed-values state."""
    if isinstance(widget, QLineEdit):
        return (
            widget.text() == ""
            and widget.placeholderText() == "-- mixed values --"
        )
    if isinstance(widget, (QDoubleSpinBox, QSpinBox)):
        return (
            widget.property("_real_min") is not None
            and widget.value() == widget.minimum()
        )
    if isinstance(widget, QComboBox):
        return (
            bool(widget.property("_has_mixed"))
            and widget.currentIndex() == 0
        )
    return False
