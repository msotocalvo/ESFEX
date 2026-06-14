"""Solar Rooftop Analysis wizard dialog.

Two-phase multi-step wizard:

Phase A — Building Potential Analysis:
  1. Define domain (rectangle on map or manual coordinates)
  2. Fetch building footprints and solar resource data
  3. Configure panel/roof/shading parameters
  4. Run analysis
  5. View and export results

Phase B — Adoption Modeling & Integration:
  6. Macroeconomic data (auto-fetch + manual edit)
  7. Adoption modeling (4 methods)
  8. Scenario comparison (chart + selection)
  9. Model integration (apply to ESFEX or export)
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.i18n import tr

from esfex.visualization.workflows.solar_rooftop_steps import (
    AnalysisStep,
    ConfigStep,
    DataSourcesStep,
    DomainStep,
    ResultsStep,
)

from esfex.visualization.workflows.solar_adoption_steps import (
    AdoptionModelStep,
    IntegrationStep,
    MacroDataStep,
    ScenarioComparisonStep,
)

# Phase A steps
_PHASE_A_NAMES = [
    lambda: tr("wizard_solar.step1"),
    lambda: tr("wizard_solar.step2"),
    lambda: tr("wizard_solar.step3"),
    lambda: tr("wizard_solar.step4"),
    lambda: tr("wizard_solar.step5"),
]

# Phase B steps
_PHASE_B_NAMES = [
    lambda: tr("wizard_solar.step6"),
    lambda: tr("wizard_solar.step7"),
    lambda: tr("wizard_solar.step8"),
    lambda: tr("wizard_solar.step9"),
]

_PHASE_A_COUNT = len(_PHASE_A_NAMES)


class SolarRooftopWizard(QDialog):
    """Multi-step wizard for solar rooftop potential analysis and adoption modeling."""

    def __init__(self, map_widget, model=None, parent=None,
                 geo_assets_provider=None):
        super().__init__(parent)
        self._geo_assets_provider = geo_assets_provider
        self.setWindowTitle(tr("wizard_solar.title"))
        self.setMinimumSize(750, 580)
        self.resize(950, 700)
        # Non-modal so the user can interact with the map while the wizard is open
        self.setModal(False)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        self._map_widget = map_widget
        self._model = model
        self._current_step = 0

        self._build_ui()
        self._update_navigation()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Step indicator bar — Phase A
        self._indicator_bar_a = QHBoxLayout()
        self._step_labels: list[QLabel] = []

        phase_a_lbl = QLabel("A")
        phase_a_lbl.setStyleSheet(
            "background-color: #2980b9; color: white; "
            "border-radius: 10px; padding: 2px 6px; font-weight: bold;"
        )
        phase_a_lbl.setFixedWidth(24)
        phase_a_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._indicator_bar_a.addWidget(phase_a_lbl)

        for i, name_fn in enumerate(_PHASE_A_NAMES):
            lbl = QLabel(f"  {i+1}. {name_fn()}  ")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                self._step_style(is_current=(i == 0), is_done=False)
            )
            self._step_labels.append(lbl)
            self._indicator_bar_a.addWidget(lbl)

        layout.addLayout(self._indicator_bar_a)

        # Phase separator
        sep_row = QHBoxLayout()
        phase_sep = QFrame()
        phase_sep.setFrameShape(QFrame.Shape.HLine)
        phase_sep.setStyleSheet("color: #444;")
        sep_row.addWidget(phase_sep)
        layout.addLayout(sep_row)

        # Step indicator bar — Phase B
        self._indicator_bar_b = QHBoxLayout()

        phase_b_lbl = QLabel("B")
        phase_b_lbl.setStyleSheet(
            "background-color: #e67e22; color: white; "
            "border-radius: 10px; padding: 2px 6px; font-weight: bold;"
        )
        phase_b_lbl.setFixedWidth(24)
        phase_b_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._indicator_bar_b.addWidget(phase_b_lbl)

        for j, name_fn in enumerate(_PHASE_B_NAMES):
            idx = _PHASE_A_COUNT + j
            lbl = QLabel(f"  {idx+1}. {name_fn()}  ")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(self._step_style(is_current=False, is_done=False))
            self._step_labels.append(lbl)
            self._indicator_bar_b.addWidget(lbl)

        layout.addLayout(self._indicator_bar_b)

        # Separator below indicators
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #555;")
        layout.addWidget(sep)

        # Stacked widget for step pages
        self._stack = QStackedWidget()

        # Phase A steps
        self._step_domain = DomainStep(
            self._map_widget, geo_assets_provider=self._geo_assets_provider)
        self._step_data = DataSourcesStep()
        self._step_config = ConfigStep()
        self._step_analysis = AnalysisStep()
        self._step_results = ResultsStep(self._map_widget)

        # Phase B steps
        self._step_macro = MacroDataStep()
        self._step_adoption = AdoptionModelStep()
        self._step_compare = ScenarioComparisonStep()
        self._step_integration = IntegrationStep(model=self._model)

        self._steps = [
            # Phase A
            self._step_domain,
            self._step_data,
            self._step_config,
            self._step_analysis,
            self._step_results,
            # Phase B
            self._step_macro,
            self._step_adoption,
            self._step_compare,
            self._step_integration,
        ]
        for step in self._steps:
            self._stack.addWidget(step)

        layout.addWidget(self._stack, 1)

        # Button row
        btn_layout = QHBoxLayout()

        self._btn_cancel = QPushButton(tr("wizard_solar.cancel"))
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self._btn_cancel)

        btn_layout.addStretch()

        self._btn_back = QPushButton(tr("wizard_solar.back"))
        self._btn_back.clicked.connect(self._go_back)
        btn_layout.addWidget(self._btn_back)

        self._btn_next = QPushButton(tr("wizard_solar.next"))
        self._btn_next.clicked.connect(self._go_next)
        btn_layout.addWidget(self._btn_next)

        layout.addLayout(btn_layout)

        # Connect step signals
        self._step_analysis.analysisFinished.connect(self._on_analysis_finished)
        self._step_adoption.modelsFinished.connect(self._on_models_finished)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _go_next(self):
        step = self._steps[self._current_step]
        if not step.is_valid():
            return

        # Transition logic
        if self._current_step == 0:
            # Domain → Data Sources: pass bounds
            self._step_data.set_bounds(self._step_domain.get_bounds())
            self._step_data.set_polygon(self._step_domain.get_polygon())
        elif self._current_step == 1:
            # Data Sources → Config: nothing to pass
            pass
        elif self._current_step == 2:
            # Config → Analysis: pass all inputs
            self._step_analysis.set_inputs(
                self._step_data.get_buildings(),
                self._step_data.get_solar_data(),
                self._step_config.get_config(),
            )
        elif self._current_step == 3:
            # Analysis → Results: pass results
            self._step_results.set_results(
                self._step_analysis.get_summary(),
                self._step_data.get_buildings(),
            )
        elif self._current_step == 4:
            # Results → Macro Data: pass bounds for country detection
            self._step_macro.set_bounds(self._step_domain.get_bounds())
        elif self._current_step == 5:
            # Macro Data → Adoption Modeling: pass macro + potential
            summary = self._step_analysis.get_summary()
            max_mw = summary.total_capacity_kwp / 1000.0 if summary else 10.0
            # Get building positions for ABM
            positions = self._get_building_positions()
            self._step_adoption.set_inputs(
                self._step_macro.get_macro_data(),
                max_mw,
                building_positions=positions,
            )
        elif self._current_step == 6:
            # Adoption Modeling → Scenario Comparison: pass curves + validation
            self._step_compare.set_curves(
                self._step_adoption.get_curves(),
                validation_data=self._step_adoption.get_validation_data(),
                max_potential_mw=self._step_adoption.get_max_potential_mw(),
            )
        elif self._current_step == 7:
            # Scenario Comparison → Integration: pass selection
            self._step_integration.set_inputs(
                selected_curve=self._step_compare.get_selected_curve(),
                all_curves=self._step_compare.get_all_curves(),
                macro=self._step_macro.get_macro_data(),
                analysis_summary=self._step_analysis.get_summary(),
            )

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

        # Update step indicator labels
        for i, lbl in enumerate(self._step_labels):
            lbl.setStyleSheet(self._step_style(
                is_current=(i == idx),
                is_done=(i < idx),
                is_phase_b=(i >= _PHASE_A_COUNT),
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
        elif idx == 3:
            # Analysis step: only enable Next when analysis is done
            self._btn_next.setText(tr("wizard_solar.next"))
            self._btn_next.setEnabled(self._step_analysis.is_valid())
        elif idx == 6:
            # Adoption step: only enable Next when models are done
            self._btn_next.setText(tr("wizard_solar.next"))
            self._btn_next.setEnabled(self._step_adoption.is_valid())
        else:
            self._btn_next.setText(tr("wizard_solar.next"))
            try:
                self._btn_next.clicked.disconnect()
            except RuntimeError:
                pass
            self._btn_next.clicked.connect(self._go_next)
            self._btn_next.setEnabled(True)

    def _on_analysis_finished(self):
        """Enable Next button when analysis completes."""
        self._btn_next.setEnabled(True)

    def _on_models_finished(self):
        """Enable Next button when adoption models complete."""
        self._btn_next.setEnabled(True)

    def _get_building_positions(self):
        """Extract building centroid positions as numpy array for ABM."""
        try:
            import numpy as np

            buildings = self._step_data.get_buildings()
            if buildings is None or buildings.empty:
                return None
            centroids = buildings.geometry.centroid
            coords = np.column_stack([centroids.y, centroids.x])
            return coords
        except Exception:
            return None

    def closeEvent(self, event):
        from esfex.visualization.workflows._wizard_utils import cleanup_wizard
        cleanup_wizard(self)
        super().closeEvent(event)

    def _cleanup_map(self):
        """Remove all temporary rooftop overlays from the map."""
        self._map_widget.clear_rooftop_domain()
        self._map_widget.clear_rooftop_results()
        self._map_widget.disable_rectangle_draw()

    def _on_cancel(self):
        self._step_macro.cancel_all()
        self._cleanup_map()
        self.reject()

    def accept(self):
        self._cleanup_map()
        super().accept()

    def reject(self):
        self._step_macro.cancel_all()
        self._cleanup_map()
        super().reject()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _step_style(
        is_current: bool, is_done: bool, is_phase_b: bool = False,
    ) -> str:
        if is_current:
            color = "#e67e22" if is_phase_b else "#2980b9"
            return (
                f"background-color: {color}; color: white; "
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
