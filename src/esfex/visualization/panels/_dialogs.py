"""Shared modal dialog helpers for the properties-panel forms.

The forms in this directory all delete entities the same way:

    reply = QMessageBox.question(
        self, tr("<form>.confirm_delete_title"),
        tr("<form>.confirm_delete_msg", ...),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        # sometimes: QMessageBox.StandardButton.No
    )
    if reply == QMessageBox.StandardButton.Yes:
        ...

Spread across ~17 forms this means 17 places where the button mask,
the default button, and the comparison constant can drift. Several
forms defaulted to "Yes" — pressing Enter on the dialog would delete
the entity, the opposite of what destructive-action dialogs should do.

``confirm_delete`` standardises:
* button set: Yes / No
* default: No (safer; matches stochastic_form which already did this)
* return value: ``True`` iff the user clicked Yes
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget


def confirm_delete(parent: QWidget, title: str, message: str) -> bool:
    """Show a Yes/No deletion-confirmation dialog. Default button: No."""
    reply = QMessageBox.question(
        parent, title, message,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return reply == QMessageBox.StandardButton.Yes
