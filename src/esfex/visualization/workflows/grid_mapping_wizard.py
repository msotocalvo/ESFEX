"""Grid Builder wizard dialog.

Multi-step wizard for building a power grid network from open geographic
databases:
  1. Region & Fetch — draw polygon, configure sources, download data
  2. Review — inspect and toggle fetched features
  3. Build & Connect — node placement, build network, auto-connect
  4. Simplify — remove empty buses, aggregate generators/batteries
  5. Demand — forecast demand per node (ML) + distribute among busbars
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.i18n import tr
from esfex.visualization.workflows.grid_mapping_steps import (
    GridMappingBuildStep,
    GridMappingDemandStep,
    GridMappingSourceFetchStep,
)

_STEP_NAMES = [
    "Region & Fetch",
    "Build & Connect",
    "Demand",
]


class GridMappingWizard(QDialog):
    """Multi-step wizard for automatic grid mapping from open databases."""

    def __init__(
        self,
        map_widget,
        model=None,
        all_states: dict | None = None,
        switch_system_fn=None,
        create_system_fn=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Grid Builder")
        self.setMinimumSize(800, 806)
        self.resize(950, 910)
        self.setModal(False)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        self._map_widget = map_widget
        self._model = model
        self._all_states = all_states if all_states is not None else {}
        self._switch_system_fn = switch_system_fn
        self._create_system_fn = create_system_fn
        self._current_step = 0
        self._fetch_done = False

        self._build_ui()
        self._update_navigation()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Step indicator bar
        self._indicator_bar = QHBoxLayout()
        self._step_labels: list[QLabel] = []
        for i, name in enumerate(_STEP_NAMES):
            lbl = QLabel(f"  {i + 1}. {name}  ")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                self._step_style(is_current=(i == 0), is_done=False)
            )
            self._step_labels.append(lbl)
            self._indicator_bar.addWidget(lbl)
        layout.addLayout(self._indicator_bar)

        # Separator
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #555;")
        layout.addWidget(sep)

        # Stacked widget for step pages
        self._stack = QStackedWidget()

        self._step_source_fetch = GridMappingSourceFetchStep(
            map_widget=self._map_widget,
        )
        self._step_build = GridMappingBuildStep(
            self._model, self._all_states,
            self._switch_system_fn, self._create_system_fn,
            map_widget=self._map_widget,
        )
        # Review and Simplify steps were dropped: Review couldn't realistically
        # be used to triage thousands of features by hand, and simplification
        # is now folded into _step_build's pipeline so the network is drawn
        # once on the final, simplified topology.
        self._step_demand = GridMappingDemandStep(
            model=self._model,
            all_states=self._all_states,
            map_widget=self._map_widget,
        )

        self._steps: list[QWidget] = [
            self._step_source_fetch,
            self._step_build,
            self._step_demand,
        ]
        for step in self._steps:
            self._stack.addWidget(step)

        layout.addWidget(self._stack, 1)

        # Button row
        btn_layout = QHBoxLayout()

        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self._btn_cancel)

        btn_layout.addStretch()

        self._btn_back = QPushButton("Back")
        self._btn_back.clicked.connect(self._go_back)
        btn_layout.addWidget(self._btn_back)

        self._btn_next = QPushButton("Next")
        self._btn_next.clicked.connect(self._go_next)
        btn_layout.addWidget(self._btn_next)

        layout.addLayout(btn_layout)

        # Connect step signals
        self._step_source_fetch.fetchFinished.connect(self._on_fetch_finished)
        self._step_build.buildFinished.connect(self._on_build_finished)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _go_next(self):
        step = self._steps[self._current_step]

        # Validate current step
        if hasattr(step, "is_valid") and not step.is_valid():
            return

        # Transition logic
        if self._current_step == 0:
            # Region & Fetch → Build & Connect: hand off the fetched
            # features straight to the builder, no manual review step.
            features = self._step_source_fetch.get_features()
            config = self._step_source_fetch.get_config()
            bounds = self._step_source_fetch.get_bounds()
            polygon = self._step_source_fetch.get_polygon()
            self._step_build.set_inputs(features, config, bounds, polygon)

        elif self._current_step == 1:
            # Build & Connect → Demand. Build step also runs auto-connect
            # + simplification before redraw.
            bounds = self._step_source_fetch.get_bounds()
            self._step_demand.set_inputs(
                bounds, self._model, self._all_states,
            )

        if self._current_step < len(self._steps) - 1:
            self._current_step += 1
            self._stack.setCurrentIndex(self._current_step)
            self._update_navigation()

    def _go_back(self):
        if self._current_step > 0:
            # Cancel any running fetchers if going back from step 0
            if self._current_step == 0:
                self._step_source_fetch.cancel_all()
            self._current_step -= 1
            self._stack.setCurrentIndex(self._current_step)
            self._update_navigation()

    def _update_navigation(self):
        idx = self._current_step
        n = len(self._steps)

        # Update step indicator
        for i, lbl in enumerate(self._step_labels):
            lbl.setStyleSheet(
                self._step_style(
                    is_current=(i == idx),
                    is_done=(i < idx),
                )
            )

        # Update buttons
        self._btn_back.setEnabled(idx > 0)
        self._btn_back.setVisible(idx > 0)

        if idx == n - 1:
            self._btn_next.setText("Close")
            try:
                self._btn_next.clicked.disconnect()
            except RuntimeError:
                pass
            self._btn_next.clicked.connect(self.accept)
        else:
            self._btn_next.setText("Next")
            try:
                self._btn_next.clicked.disconnect()
            except RuntimeError:
                pass
            self._btn_next.clicked.connect(self._go_next)
            # Next requires fetch completed on step 0
            if idx == 0 and not self._fetch_done:
                self._btn_next.setEnabled(False)
            else:
                self._btn_next.setEnabled(True)

    def _on_fetch_finished(self):
        """Enable Next button when all fetchers complete."""
        self._fetch_done = True
        if self._current_step == 0:
            self._btn_next.setEnabled(True)

    def _on_build_finished(self):
        """Update button after network is built."""
        self._btn_next.setEnabled(True)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        from esfex.visualization.workflows._wizard_utils import cleanup_wizard
        cleanup_wizard(self)
        super().closeEvent(event)

    def _cleanup_map(self):
        """Remove temporary overlays from the map."""
        try:
            self._map_widget.clear_domain_polygon()
        except Exception:
            pass
        try:
            self._map_widget.disable_domain_polygon_draw()
        except Exception:
            pass
        try:
            self._step_demand.cleanup_map()
        except Exception:
            pass

    def _on_cancel(self):
        self.reject()

    def accept(self):
        self._cleanup_map()
        super().accept()

    def reject(self):
        # Stop the workers of *every* step (clustering, building fetch,
        # classify) — not just the source-fetch step — so none is left
        # running when the dialog is torn down. cleanup_wizard also runs
        # _cleanup_map via its hook.
        from esfex.visualization.workflows._wizard_utils import cleanup_wizard
        cleanup_wizard(self)
        super().reject()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _step_style(is_current: bool, is_done: bool) -> str:
        if is_current:
            return (
                "background-color: #e67e22; color: white; "
                "border-radius: 4px; padding: 4px 8px; font-weight: bold;"
            )
        if is_done:
            return (
                "background-color: #27ae60; color: white; "
                "border-radius: 4px; padding: 4px 8px;"
            )
        return (
            "background-color: #555; color: #aaa; "
            "border-radius: 4px; padding: 4px 8px;"
        )
