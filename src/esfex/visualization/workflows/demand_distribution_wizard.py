"""Demand Distribution Analysis wizard dialog.

Multi-step wizard for distributing node demand among existing busbars
using building footprint data and spatial clustering:
1. Select target nodes (multi-system)
2. Define domain & fetch buildings
3. Classify buildings by type (compute relative weights)
4. Spatial clustering
5. Review per-node bus ↔ cluster mapping & apply changes
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

from esfex.visualization.workflows.demand_distribution_steps import (
    ClassificationStep,
    ClusteringStep,
    DomainFetchStep,
    ReviewApplyStep,
    TargetSelectionStep,
)

_STEP_NAMES = [
    lambda: tr("wizard_demand.step1"),
    lambda: tr("wizard_demand.step2"),
    lambda: tr("wizard_demand.step3"),
    lambda: tr("wizard_demand.step4"),
    lambda: tr("wizard_demand.step5"),
]


class DemandDistributionWizard(QDialog):
    """Multi-step wizard for demand distribution analysis."""

    def __init__(
        self,
        model,
        all_states: dict,
        current_system_name: str,
        map_widget,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("wizard_demand.title"))
        self.setMinimumSize(800, 620)
        self.resize(950, 700)
        self.setModal(False)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        self._model = model
        self._all_states = all_states
        self._current_system_name = current_system_name
        self._map_widget = map_widget
        self._current_step = 0

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
        for i, name_fn in enumerate(_STEP_NAMES):
            lbl = QLabel(f"  {i + 1}. {name_fn()}  ")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(self._step_style(is_current=(i == 0), is_done=False))
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

        self._step_target = TargetSelectionStep(self._all_states)
        self._step_domain = DomainFetchStep(self._map_widget)
        self._step_classify = ClassificationStep()
        self._step_cluster = ClusteringStep(self._map_widget)
        self._step_review_apply = ReviewApplyStep(
            self._model, self._all_states, self._current_system_name
        )

        self._steps = [
            self._step_target,
            self._step_domain,
            self._step_classify,
            self._step_cluster,
            self._step_review_apply,
        ]
        for step in self._steps:
            self._stack.addWidget(step)

        layout.addWidget(self._stack, 1)

        # Button row
        btn_layout = QHBoxLayout()

        self._btn_cancel = QPushButton(tr("wizard_demand.cancel"))
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self._btn_cancel)

        btn_layout.addStretch()

        self._btn_back = QPushButton(tr("wizard_demand.back"))
        self._btn_back.clicked.connect(self._go_back)
        btn_layout.addWidget(self._btn_back)

        self._btn_next = QPushButton(tr("wizard_demand.next"))
        self._btn_next.clicked.connect(self._go_next)
        btn_layout.addWidget(self._btn_next)

        layout.addLayout(btn_layout)

        # Populate target tree
        self._step_target.populate()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _go_next(self):
        step = self._steps[self._current_step]
        if not step.is_valid():
            return

        # Transition logic between steps
        if self._current_step == 0:
            # Target → Domain: nothing extra
            pass
        elif self._current_step == 1:
            # Domain → Classification: pass buildings
            self._step_classify.set_buildings(self._step_domain.get_buildings())
        elif self._current_step == 2:
            # Classification → Clustering: pass classified buildings
            self._step_cluster.set_classified_buildings(
                self._step_classify.get_classified_buildings()
            )
            # Set default cluster count from max bus count across targets
            targets = self._step_target.get_selected_targets()
            max_buses = max((len(t["buses"]) for t in targets), default=3)
            if max_buses > 1:
                self._step_cluster.set_default_clusters(max_buses)
        elif self._current_step == 3:
            # Clustering → Review & Apply: pass summary + targets
            summary = self._step_cluster.get_cluster_summary()
            targets = self._step_target.get_selected_targets()
            self._step_review_apply.set_data(targets, summary)

        if self._current_step < len(self._steps) - 1:
            self._current_step += 1
            self._stack.setCurrentIndex(self._current_step)
            self._update_navigation()

    def _go_back(self):
        if self._current_step > 0:
            self._current_step -= 1
            self._stack.setCurrentIndex(self._current_step)
            self._update_navigation()

    def _update_navigation(self):
        idx = self._current_step
        n = len(self._steps)

        # Update step indicator
        for i, lbl in enumerate(self._step_labels):
            lbl.setStyleSheet(self._step_style(
                is_current=(i == idx),
                is_done=(i < idx),
            ))

        # Update buttons
        self._btn_back.setEnabled(idx > 0)
        self._btn_back.setVisible(idx > 0)

        if idx == n - 1:
            self._btn_next.setText(tr("common.close"))
            try:
                self._btn_next.clicked.disconnect()
            except RuntimeError:
                pass
            self._btn_next.clicked.connect(self.accept)
        else:
            self._btn_next.setText(tr("wizard_demand.next"))
            try:
                self._btn_next.clicked.disconnect()
            except RuntimeError:
                pass
            self._btn_next.clicked.connect(self._go_next)
            self._btn_next.setEnabled(True)

    def closeEvent(self, event):
        from esfex.visualization.workflows._wizard_utils import cleanup_wizard
        cleanup_wizard(self)
        super().closeEvent(event)

    def _cleanup_map(self):
        """Remove all temporary overlays from the map."""
        self._map_widget.clear_demand_domain()
        self._map_widget.clear_demand_clusters()

    def _on_cancel(self):
        self._cleanup_map()
        self.reject()

    def accept(self):
        self._cleanup_map()
        super().accept()

    def reject(self):
        self._cleanup_map()
        super().reject()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _step_style(is_current: bool, is_done: bool) -> str:
        if is_current:
            return (
                "background-color: #2980b9; color: white; "
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
