"""EV & V2G Assessment Workflow wizard dialog.

Two-phase wizard: Phase A (Fleet Assessment, steps 1-5) and
Phase B (V2G Analysis & Grid Integration, steps 6-9).
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.i18n import tr

logger = logging.getLogger(__name__)

# -- Phase / step definitions ----------------------------------------

_PHASE_A_STEPS = [
    ("wizard_ev.step_domain", "Domain"),
    ("wizard_ev.step_macro", "Macro & Policy"),
    ("wizard_ev.step_adoption", "Adoption Models"),
    ("wizard_ev.step_results", "Fleet Results"),
    ("wizard_ev.step_scenario", "Scenario Select"),
]

_PHASE_B_STEPS = [
    ("wizard_ev.step_charging", "Charging Demand"),
    ("wizard_ev.step_v2g", "V2G Potential"),
    ("wizard_ev.step_grid", "Grid Impact"),
    ("wizard_ev.step_integration", "Integration"),
]

_COLOR_A = "#8e44ad"   # purple
_COLOR_B = "#16a085"   # teal
_COLOR_DONE = "#27ae60"
_COLOR_PENDING = "#555"


class EVWizardDialog(QDialog):
    """Main wizard dialog for EV & V2G Assessment."""

    def __init__(self, map_widget, model=None, parent=None):
        super().__init__(parent)
        self._map_widget = map_widget
        self._model = model
        self._current_step = 0
        self._total_steps = len(_PHASE_A_STEPS) + len(_PHASE_B_STEPS)

        self.setWindowTitle(tr("wizard_ev.title"))
        self.resize(900, 700)
        self.setMinimumSize(780, 550)

        main_layout = QVBoxLayout(self)

        # -- Step indicator bars --
        self._phase_a_labels: list[QLabel] = []
        self._phase_b_labels: list[QLabel] = []

        # Phase A bar
        bar_a = QHBoxLayout()
        badge_a = self._make_badge("A", _COLOR_A)
        bar_a.addWidget(badge_a)
        for i, (key, fallback) in enumerate(_PHASE_A_STEPS):
            lbl = QLabel(tr(key) if tr(key) != key else fallback)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            lbl.setFixedHeight(28)
            self._phase_a_labels.append(lbl)
            bar_a.addWidget(lbl)
        main_layout.addLayout(bar_a)

        # Phase B bar
        bar_b = QHBoxLayout()
        badge_b = self._make_badge("B", _COLOR_B)
        bar_b.addWidget(badge_b)
        for i, (key, fallback) in enumerate(_PHASE_B_STEPS):
            lbl = QLabel(tr(key) if tr(key) != key else fallback)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            lbl.setFixedHeight(28)
            self._phase_b_labels.append(lbl)
            bar_b.addWidget(lbl)
        main_layout.addLayout(bar_b)

        # -- Stacked widget for step pages --
        self._stack = QStackedWidget()
        main_layout.addWidget(self._stack, 1)

        # -- Create step widgets --
        self._create_steps()

        # -- Navigation buttons --
        nav = QHBoxLayout()
        self._btn_cancel = QPushButton(tr("wizard_common.cancel"))
        self._btn_cancel.clicked.connect(self._on_cancel)
        nav.addWidget(self._btn_cancel)

        nav.addStretch()

        self._btn_back = QPushButton(tr("wizard_common.back"))
        self._btn_back.clicked.connect(self._go_back)
        nav.addWidget(self._btn_back)

        self._btn_next = QPushButton(tr("wizard_common.next"))
        self._btn_next.clicked.connect(self._go_next)
        nav.addWidget(self._btn_next)

        main_layout.addLayout(nav)

        # Initial state
        self._update_indicators()
        self._update_buttons()

    @staticmethod
    def _make_badge(text: str, color: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFixedSize(24, 24)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"background-color: {color}; color: white; font-weight: bold;"
            f"border-radius: 12px; font-size: 12px;"
        )
        return lbl

    def _create_steps(self):
        from esfex.visualization.workflows.ev_steps import (
            EVAdoptionModelStep,
            EVDomainStep,
            EVFleetResultsStep,
            EVMacroDataStep,
            EVScenarioSelectionStep,
        )
        from esfex.visualization.workflows.ev_advanced_steps import (
            EVChargingDemandStep,
            EVGridImpactStep,
            EVIntegrationStep,
            EVV2GPotentialStep,
        )

        # Phase A
        self._step_domain = EVDomainStep(self._map_widget, parent=self)
        self._step_macro = EVMacroDataStep(parent=self)
        self._step_adoption = EVAdoptionModelStep(parent=self)
        self._step_results = EVFleetResultsStep(parent=self)
        self._step_scenario = EVScenarioSelectionStep(parent=self)

        # Phase B
        self._step_charging = EVChargingDemandStep(parent=self)
        self._step_v2g = EVV2GPotentialStep(parent=self)
        self._step_grid = EVGridImpactStep(parent=self)
        self._step_integration = EVIntegrationStep(model=self._model, parent=self)

        # Connect adoption finished → enable Next
        self._step_adoption.modelsFinished.connect(self._on_models_finished)

        # Add to stack
        self._steps = [
            self._step_domain,      # 0
            self._step_macro,       # 1
            self._step_adoption,    # 2
            self._step_results,     # 3
            self._step_scenario,    # 4
            self._step_charging,    # 5
            self._step_v2g,         # 6
            self._step_grid,        # 7
            self._step_integration, # 8
        ]
        for step in self._steps:
            self._stack.addWidget(step)

    def _on_models_finished(self):
        """Re-enable navigation after adoption models complete."""
        self._btn_next.setEnabled(True)

    # -- Navigation --------------------------------------------------

    def _go_next(self):
        step = self._steps[self._current_step]
        if not step.is_valid():
            return

        # Propagate data to next step
        self._propagate_forward()

        if self._current_step < self._total_steps - 1:
            self._current_step += 1
            self._stack.setCurrentIndex(self._current_step)
            self._update_indicators()
            self._update_buttons()
        else:
            # Last step — close
            self.accept()

    def _go_back(self):
        if self._current_step > 0:
            self._current_step -= 1
            self._stack.setCurrentIndex(self._current_step)
            self._update_indicators()
            self._update_buttons()

    def _on_cancel(self):
        self._cleanup()
        self.reject()

    def _propagate_forward(self):
        """Pass data from current step to the next step."""
        idx = self._current_step

        if idx == 0:
            # Domain → Macro: pass bounds for country detection
            bounds = self._step_domain.get_bounds()
            self._step_macro.set_bounds(bounds)

        elif idx == 1:
            # Macro → Adoption: pass inputs
            macro = self._step_macro.get_ev_macro_data()
            transport = self._step_domain.get_transport_context()
            self._step_adoption.set_inputs(macro, transport)

        elif idx == 2:
            # Adoption → Results: pass curves
            curves = self._step_adoption.get_curves()
            validation = self._step_adoption.get_validation_data()
            self._step_results.set_results(curves, validation)

        elif idx == 3:
            # Results → Scenario Selection: pass curves
            curves = self._step_adoption.get_curves()
            validation = self._step_adoption.get_validation_data()
            self._step_scenario.set_curves(curves, validation)

        elif idx == 4:
            # Scenario Selection → Charging Demand: pass selected curve
            curve = self._step_scenario.get_selected_curve()
            self._step_charging.set_curve(curve)

        elif idx == 5:
            # Charging Demand → V2G: pass fleet at year
            fleet = self._step_charging.get_fleet_at_year()
            self._step_v2g.set_fleet(fleet)

        elif idx == 6:
            # V2G → Grid Impact: pass scenarios + V2G + base demand
            scenarios = self._step_charging.get_scenarios()
            v2g = self._step_v2g.get_v2g_potential()
            base_demand = self._step_charging.get_base_demand()
            self._step_grid.set_inputs(scenarios, v2g, base_demand)

        elif idx == 7:
            # Grid Impact → Integration: pass all data
            curve = self._step_scenario.get_selected_curve()
            transport = self._step_domain.get_transport_context()
            macro = self._step_macro.get_ev_macro_data()
            v2g = self._step_v2g.get_v2g_potential()
            degradation = self._step_v2g.get_degradation()
            grid_impact = self._step_grid.get_result()
            scenarios = self._step_charging.get_scenarios()
            self._step_integration.set_inputs(
                curve, transport, macro, v2g, degradation, grid_impact, scenarios,
            )

    # -- Visual indicators -------------------------------------------

    def _update_indicators(self):
        phase_a_count = len(_PHASE_A_STEPS)

        for i, lbl in enumerate(self._phase_a_labels):
            if i < self._current_step:
                self._style_label(lbl, _COLOR_DONE, "white", bold=False)
            elif i == self._current_step:
                self._style_label(lbl, _COLOR_A, "white", bold=True)
            else:
                self._style_label(lbl, _COLOR_PENDING, "#aaa", bold=False)

        for i, lbl in enumerate(self._phase_b_labels):
            global_idx = phase_a_count + i
            if global_idx < self._current_step:
                self._style_label(lbl, _COLOR_DONE, "white", bold=False)
            elif global_idx == self._current_step:
                self._style_label(lbl, _COLOR_B, "white", bold=True)
            else:
                self._style_label(lbl, _COLOR_PENDING, "#aaa", bold=False)

    @staticmethod
    def _style_label(lbl: QLabel, bg: str, fg: str, bold: bool = False):
        weight = "bold" if bold else "normal"
        lbl.setStyleSheet(
            f"background-color: {bg}; color: {fg};"
            f"font-weight: {weight}; padding: 4px 8px;"
            f"border-radius: 4px; font-size: 11px;"
        )

    def _update_buttons(self):
        self._btn_back.setEnabled(self._current_step > 0)

        if self._current_step == self._total_steps - 1:
            self._btn_next.setText(tr("wizard_common.close"))
        else:
            self._btn_next.setText(tr("wizard_common.next"))

        # Disable Next during adoption run (step 2) until models finish
        if self._current_step == 2:
            worker = getattr(self._step_adoption, "_worker", None)
            if worker and worker.isRunning():
                self._btn_next.setEnabled(False)
            else:
                self._btn_next.setEnabled(True)
        else:
            self._btn_next.setEnabled(True)

    # -- Cleanup -----------------------------------------------------

    def _cleanup(self):
        """Cancel running fetchers/workers."""
        for step in self._steps:
            cancel = getattr(step, "cancel_all", None)
            if callable(cancel):
                cancel()

    def closeEvent(self, event):
        self._cleanup()
        super().closeEvent(event)
