"""Properties panel with stacked forms for different element types."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.i18n import tr
from esfex.visualization.modern_widgets import CollapsibleSection


def _collapsify_groups(widget: QWidget) -> None:
    """Wrap top-level QGroupBox children in CollapsibleSection widgets."""
    layout = widget.layout()
    if layout is None or not isinstance(layout, QVBoxLayout):
        return

    i = 0
    while i < layout.count():
        item = layout.itemAt(i)
        w = item.widget() if item else None
        if isinstance(w, QGroupBox):
            title = w.title()
            layout.removeWidget(w)
            section = CollapsibleSection(title=title, expanded=True)
            w.setTitle("")
            w.setFlat(True)
            w.setStyleSheet(
                "QGroupBox { border: none; margin: 0; padding: 0; }"
            )
            section.content_layout.setContentsMargins(0, 0, 0, 0)
            section.content_layout.addWidget(w)
            layout.insertWidget(i, section)
        elif isinstance(w, QTabWidget):
            for t in range(w.count()):
                _collapsify_groups(w.widget(t))
        i += 1


class PropertiesPanel(QWidget):
    """Right-side panel that displays property forms for selected elements."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._title = QLabel(tr("properties.title"))
        self._title.setObjectName("panelTitle")
        layout.addWidget(self._title)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self._stack = QStackedWidget()
        self._stack.currentChanged.connect(self._adjust_stack_size)
        self._scroll.setWidget(self._stack)
        layout.addWidget(self._scroll)

        # Placeholder widget shown when nothing is selected
        self._empty = QLabel(tr("properties.empty"))
        self._empty.setWordWrap(True)
        self._stack.addWidget(self._empty)

        # Form widgets will be registered here by type
        self._forms: dict[str, QWidget] = {}
        # Lazy factories: element_type -> (factory, post_create_callback)
        self._factories: dict[str, tuple[Callable[[], QWidget], Optional[Callable[[QWidget], None]]]] = {}
        # Permanent factory store for retranslation (never popped)
        self._all_factories: dict[str, tuple[Callable[[], QWidget], Optional[Callable[[QWidget], None]]]] = {}

    def _adjust_stack_size(self, index: int):
        """Resize the stacked widget to fit the current form's content."""
        widget = self._stack.widget(index)
        if widget:
            hint = widget.sizeHint()
            if hint.height() > 20:
                self._stack.setMinimumHeight(0)
                self._stack.setMaximumHeight(hint.height())
            else:
                self._stack.setMaximumHeight(16777215)
        else:
            self._stack.setMaximumHeight(16777215)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_form(
        self,
        element_type: str,
        form_or_factory,
        post_create: Optional[Callable[[QWidget], None]] = None,
    ):
        """Register a form widget or lazy factory for a given element type.

        Parameters
        ----------
        element_type : str
            Key used to look up this form (e.g. ``"node"``, ``"generator"``).
        form_or_factory : QWidget | callable
            Either an already-instantiated widget or a zero-arg callable
            that returns one.  Callables are only invoked the first time
            the form is actually needed.
        post_create : callable, optional
            If *form_or_factory* is a callable, this callback is invoked
            with the freshly-created widget so the caller can connect
            signals, store references, etc.
        """
        if callable(form_or_factory) and not isinstance(form_or_factory, QWidget):
            self._factories[element_type] = (form_or_factory, post_create)
            self._all_factories[element_type] = (form_or_factory, post_create)
        else:
            self._forms[element_type] = form_or_factory
            self._stack.addWidget(form_or_factory)

    def _materialize(self, element_type: str) -> QWidget | None:
        """Instantiate a lazy form and promote it to ``_forms``."""
        entry = self._factories.pop(element_type, None)
        if entry is None:
            return None
        factory, post_create = entry
        form = factory()
        _collapsify_groups(form)
        self._forms[element_type] = form
        self._stack.addWidget(form)
        if post_create is not None:
            post_create(form)
        return form

    def get_form(self, element_type: str) -> QWidget | None:
        """Return the form for *element_type*, materializing it if needed."""
        form = self._forms.get(element_type)
        if form is None:
            form = self._materialize(element_type)
        return form

    def show_element(self, element_type: str, element_id: str):
        """Show the form for the given element type and populate it."""
        form = self._forms.get(element_type)
        if form is None:
            form = self._materialize(element_type)
        if form:
            pretty = element_type.replace("_", " ").title()
            # For singleton forms (global_settings, stochastic, etc.)
            # avoid redundant "Global Settings: global_settings" headers
            if element_id == element_type or not element_id:
                self._title.setText(pretty)
            else:
                self._title.setText(f"{pretty}: {element_id}")
            self._stack.setCurrentWidget(form)
            # Forms should implement a `load_element(element_id)` method
            if hasattr(form, "load_element"):
                form.load_element(element_id)
        else:
            self.clear()

    def show_node(self, node_id: int):
        """Convenience method for showing a node's properties."""
        self.show_element("node", str(node_id))

    def show_elements(self, element_type: str, element_ids: list[str]):
        """Show form for multiple elements of the same type (multi-edit)."""
        form = self._forms.get(element_type)
        if form is None:
            form = self._materialize(element_type)
        if form and hasattr(form, "load_elements"):
            pretty = element_type.replace("_", " ").title()
            self._title.setText(f"{pretty}: {tr('properties.n_selected', n=len(element_ids))}")
            self._stack.setCurrentWidget(form)
            form.load_elements(element_ids)
        else:
            self.clear()

    def clear(self):
        """Show the empty placeholder."""
        self._title.setText(tr("properties.title"))
        self._stack.setCurrentWidget(self._empty)

    def retranslateUi(self):
        """Recreate all materialized forms with fresh translations."""
        self._empty.setText(tr("properties.empty"))
        self._title.setText(tr("properties.title"))

        # Collect forms that have factories (can be recreated)
        to_recreate = [
            etype for etype in list(self._forms.keys())
            if etype in self._all_factories
        ]

        # Destroy old forms and put factories back
        for etype in to_recreate:
            old = self._forms.pop(etype)
            self._stack.removeWidget(old)
            old.deleteLater()
            self._factories[etype] = self._all_factories[etype]

        # Re-materialize (post_create callbacks update external references)
        for etype in to_recreate:
            self._materialize(etype)

        self._stack.setCurrentWidget(self._empty)
