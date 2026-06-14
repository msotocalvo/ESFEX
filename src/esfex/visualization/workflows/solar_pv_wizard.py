"""Solar PV Potential Assessment wizard dialog.

Two-phase multi-step wizard:

Phase A — Solar PV Resource Assessment & MCDA:
  1. Define domain (rectangle on map or manual coordinates)
  2. Configure module (CEC database) and assessment parameters
  3. Configure MCDA criteria and weighting method
  4. Run analysis (ERA5 + DEM + LULC + MCDA)
  5. View results and generate development zones

Phase B — Advanced Analysis:
  6. Solar characterization (GHI patterns, diurnal, seasonal, temperature)
  7. Financial analysis (LCOE, NPV, IRR, sensitivity)
  8. Array / shading analysis (GCR, inter-row shading, bifacial gain)
  9. Availability profile generation (hourly CF for model generators)
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

from esfex.visualization.workflows.solar_pv_steps import (
    SolarPVAnalysisStep,
    SolarPVConfigStep,
    SolarPVCriteriaStep,
    SolarPVDomainStep,
    SolarPVResultsStep,
)

from esfex.visualization.workflows.solar_pv_advanced_steps import (
    SolarArrayStep,
    SolarAvailabilityStep,
    SolarCharacterizationStep,
    SolarFinancialStep,
)

# Phase A steps
_PHASE_A_NAMES = [
    lambda: tr("wizard_solar_pv.step1"),
    lambda: tr("wizard_solar_pv.step2"),
    lambda: tr("wizard_solar_pv.step3"),
    lambda: tr("wizard_solar_pv.step4"),
    lambda: tr("wizard_solar_pv.step5"),
]

# Phase B steps
_PHASE_B_NAMES = [
    lambda: tr("wizard_solar_pv.step6"),
    lambda: tr("wizard_solar_pv.step7"),
    lambda: tr("wizard_solar_pv.step8"),
    lambda: tr("wizard_solar_pv.step9"),
]

_PHASE_A_COUNT = len(_PHASE_A_NAMES)


class SolarPVWizard(QDialog):
    """Multi-step wizard for solar PV assessment with MCDA and advanced analysis."""

    def __init__(self, map_widget, model=None, parent=None,
                 geo_assets_provider=None):
        super().__init__(parent)
        self._geo_assets_provider = geo_assets_provider
        self.setWindowTitle(tr("wizard_solar_pv.title"))
        self.setMinimumSize(750, 580)
        self.resize(950, 700)
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
            "background-color: #e67e22; color: white; "
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
            "background-color: #16a085; color: white; "
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
        self._step_domain = SolarPVDomainStep(
            self._map_widget, geo_assets_provider=self._geo_assets_provider)
        self._step_config = SolarPVConfigStep()
        self._step_criteria = SolarPVCriteriaStep()
        self._step_analysis = SolarPVAnalysisStep()
        self._step_results = SolarPVResultsStep(self._map_widget, self._model)

        # Phase B steps
        self._step_characterization = SolarCharacterizationStep()
        self._step_financial = SolarFinancialStep()
        self._step_array = SolarArrayStep()
        self._step_availability = SolarAvailabilityStep(model=self._model)

        self._steps = [
            # Phase A
            self._step_domain,
            self._step_config,
            self._step_criteria,
            self._step_analysis,
            self._step_results,
            # Phase B
            self._step_characterization,
            self._step_financial,
            self._step_array,
            self._step_availability,
        ]
        for step in self._steps:
            self._stack.addWidget(step)

        layout.addWidget(self._stack, 1)

        # Button row
        btn_layout = QHBoxLayout()

        self._btn_cancel = QPushButton(tr("wizard_solar_pv.cancel"))
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self._btn_cancel)

        btn_layout.addStretch()

        self._btn_back = QPushButton(tr("wizard_solar_pv.back"))
        self._btn_back.clicked.connect(self._go_back)
        btn_layout.addWidget(self._btn_back)

        self._btn_next = QPushButton(tr("wizard_solar_pv.next"))
        self._btn_next.clicked.connect(self._go_next)
        btn_layout.addWidget(self._btn_next)

        layout.addLayout(btn_layout)

        # Connect step signals
        self._step_analysis.analysisFinished.connect(self._on_analysis_finished)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _go_next(self):
        step = self._steps[self._current_step]
        if not step.is_valid():
            return

        # Transition logic
        if self._current_step == 0:
            # Domain → Config: nothing to pass
            pass
        elif self._current_step == 1:
            # Config → Criteria: nothing to pass
            pass
        elif self._current_step == 2:
            # Criteria → Analysis: pass all inputs
            transmission_lines = self._get_transmission_lines()
            self._step_analysis.set_inputs(
                self._step_domain.get_bounds(),
                self._step_config.get_config(),
                self._step_criteria.get_config(),
                transmission_lines,
                polygon=self._step_domain.get_polygon(),
            )
        elif self._current_step == 3:
            # Analysis → Results: pass summary + config
            self._step_results.set_results(
                self._step_analysis.get_summary(),
                self._step_config.get_config(),
            )
        elif self._current_step == 4:
            # Results → Characterization: pass hourly data
            summary = self._step_analysis.get_summary()
            hourly_data = summary.hourly_data if summary else None
            config = self._step_config.get_config()
            self._step_characterization.set_inputs(hourly_data, summary, config)
        elif self._current_step == 5:
            # Characterization → Financial: pass capacity and CF
            summary = self._step_analysis.get_summary()
            config = self._step_config.get_config()
            # Estimate capacity from feasible area
            capacity_mw = summary.total_capacity_mw if summary else 10.0
            cf_avg = summary.cf_avg if summary else 0.20
            workers = config.effective_workers if config else 0
            self._step_financial.set_inputs(capacity_mw, cf_avg, workers)
        elif self._current_step == 6:
            # Financial → Array: pass latitude, tilt, CF, bifacial
            config = self._step_config.get_config()
            summary = self._step_analysis.get_summary()
            bounds = self._step_domain.get_bounds()
            latitude = (bounds[0] + bounds[2]) / 2.0 if bounds else 0.0

            # Determine tilt
            if config.orientation == "custom":
                tilt = config.tilt
            else:
                tilt = abs(latitude)  # latitude-optimal

            module = self._step_config.get_module_spec()
            is_bifacial = module.bifacial if module else False
            workers = config.effective_workers if config else 0

            self._step_array.set_inputs(
                latitude=latitude,
                tilt=tilt,
                capacity_factor=summary.cf_avg if summary else 0.20,
                capacity_mw=summary.total_capacity_mw if summary else 10.0,
                is_bifacial=is_bifacial,
                max_workers=workers,
            )
        elif self._current_step == 7:
            # Array → Availability: pass hourly data and config
            summary = self._step_analysis.get_summary()
            hourly_data = summary.hourly_data if summary else None
            config = self._step_config.get_config()
            self._step_availability.set_inputs(hourly_data, config, summary)

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
            self._btn_next.setText(tr("wizard_solar_pv.next"))
            self._btn_next.setEnabled(self._step_analysis.is_valid())
        else:
            self._btn_next.setText(tr("wizard_solar_pv.next"))
            try:
                self._btn_next.clicked.disconnect()
            except RuntimeError:
                pass
            self._btn_next.clicked.connect(self._go_next)
            self._btn_next.setEnabled(True)

    def _on_analysis_finished(self):
        """Enable Next button when analysis completes."""
        self._btn_next.setEnabled(True)

    def _get_transmission_lines(self) -> list:
        """Extract transmission line coordinates from the GUI model."""
        if self._model is None:
            return []

        lines = []
        try:
            state = self._model.state
            for line in state.transmission_lines:
                coords = []
                for pt in line.trace:
                    coords.append([pt.lat, pt.lng])
                if coords:
                    lines.append({"coords": coords})
        except Exception:
            pass
        return lines

    def closeEvent(self, event):
        from esfex.visualization.workflows._wizard_utils import cleanup_wizard
        cleanup_wizard(self)
        super().closeEvent(event)

    def _cleanup_map(self):
        """Remove all temporary solar PV overlays from the map."""
        self._map_widget.clear_solar_pv_domain()
        self._map_widget.clear_solar_pv_results()
        self._map_widget.clear_solar_pv_dev_zones()
        self._map_widget.disable_rectangle_draw()

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
    def _step_style(
        is_current: bool, is_done: bool, is_phase_b: bool = False,
    ) -> str:
        if is_current:
            color = "#16a085" if is_phase_b else "#e67e22"
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
