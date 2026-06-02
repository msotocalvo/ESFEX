"""Demand Estimation wizard dialog.

Multi-step wizard for estimating electricity demand time series
from spatial proxies, macroeconomic indicators, and meteorological data.

Steps:
  1. Scope & Config     — study area, node selection, configuration
  2. Data Acquisition   — fetch proxies, macro indicators, ERA5 meteo
  3. Projections Table  — year-by-year GDP, population, elasticity
  4. Build Profiles     — run estimation engine (background computation)
  5. Calibrate & Export — validate, calibrate, export per-node CSV files
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
)

from esfex.visualization.i18n import tr
from esfex.visualization.theme import current_theme
from esfex.visualization.workflows.demand_estimation_steps import (
    CalibrationStep,
    MacroEconomicStep,
    ProxyDataStep,
    ScopeTargetStep,
)

_STEP_NAMES = [
    lambda: tr("wizard_demest.step1"),
    lambda: tr("wizard_demest.step2"),
    lambda: tr("wizard_demest.step3"),
    lambda: tr("wizard_demest.step4"),
]


class DemandEstimationWizard(QDialog):
    """Multi-step wizard for estimating electricity demand time series."""

    def __init__(self, map_widget=None, all_states: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("wizard_demest.title"))
        self.setMinimumSize(800, 832)
        self.resize(980, 962)
        self.setModal(False)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        self._map_widget = map_widget
        self._all_states = all_states or {}
        self._current_step = 0

        self._build_ui()
        self._update_navigation()

    # ──────────────────────────────────────────────────────────────────────────
    # UI Construction
    # ──────────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ── Step indicator bar (single row, all steps equal) ──
        indicator_bar = QHBoxLayout()
        self._step_labels: list[QLabel] = []

        for i, name_fn in enumerate(_STEP_NAMES):
            lbl = QLabel(f"  {i + 1}. {name_fn()}  ")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._step_labels.append(lbl)
            indicator_bar.addWidget(lbl)

        layout.addLayout(indicator_bar)

        # ── Step pages ──
        self._stack = QStackedWidget()

        self._step_scope = ScopeTargetStep(
            all_states=self._all_states, map_widget=self._map_widget
        )
        self._step_proxy = ProxyDataStep()
        self._step_macro = MacroEconomicStep()
        self._step_calib = CalibrationStep(all_states=self._all_states)

        self._steps = [
            self._step_scope,
            self._step_proxy,
            self._step_macro,
            self._step_calib,
        ]
        for step in self._steps:
            self._stack.addWidget(step)

        layout.addWidget(self._stack, 1)

        # ── Navigation buttons ──
        btn_layout = QHBoxLayout()

        self._btn_cancel = QPushButton(tr("wizard_demest.cancel"))
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self._btn_cancel)

        self._btn_export = QPushButton(tr("wizard_demest.export_csv"))
        self._btn_export.clicked.connect(self._on_export)
        self._btn_export.setVisible(False)
        btn_layout.addWidget(self._btn_export)

        self._export_status = QLabel("")
        self._export_status.setStyleSheet("color: #aaa; font-size: 10px;")
        self._export_status.setVisible(False)
        btn_layout.addWidget(self._export_status)

        btn_layout.addStretch()

        self._btn_back = QPushButton(tr("wizard_demest.back"))
        self._btn_back.clicked.connect(self._go_back)
        btn_layout.addWidget(self._btn_back)

        self._btn_next = QPushButton(tr("wizard_demest.next"))
        self._btn_next.clicked.connect(self._go_next)
        btn_layout.addWidget(self._btn_next)

        layout.addLayout(btn_layout)

        # ── Connect step signals to update Next button reactively ──
        self._step_scope.validityChanged.connect(self._refresh_next_enabled)
        self._step_proxy.validityChanged.connect(self._refresh_next_enabled)
        self._step_macro.buildFinished.connect(self._refresh_next_enabled)

    # ──────────────────────────────────────────────────────────────────────────
    # Navigation
    # ──────────────────────────────────────────────────────────────────────────

    def _go_next(self) -> None:
        step = self._steps[self._current_step]
        if not step.is_valid():
            return

        self._propagate_forward()

        if self._current_step < len(self._steps) - 1:
            self._current_step += 1
            self._stack.setCurrentIndex(self._current_step)
            self._update_navigation()

    def _go_back(self) -> None:
        if self._current_step > 0:
            self._current_step -= 1
            self._stack.setCurrentIndex(self._current_step)
            self._update_navigation()

    def _propagate_forward(self) -> None:
        """Pass data from the current step to the next step."""
        idx = self._current_step

        if idx == 0:
            # Scope → Data Acquisition
            nodes = self._step_scope.get_selected_nodes()
            if nodes:
                lats = [n["lat"] for n in nodes]
                lons = [n["lon"] for n in nodes]
                pad = 0.05
                s = min(lats) - pad
                n_ = max(lats) + pad
                w = min(lons) - pad
                e = max(lons) + pad
                if self._step_scope.get_bounds() is None:
                    self._step_scope._set_bounds(s, w, n_, e)
                self._step_proxy.set_location(
                    sum(lats) / len(lats),
                    sum(lons) / len(lons),
                )
                self._step_proxy.set_weather_year(
                    self._step_scope.get_base_year() - 3
                )
            bounds = self._step_scope.get_bounds()
            if bounds:
                self._step_proxy._set_bounds(*bounds)
            countries = self._step_scope.get_system_countries()
            self._step_proxy.set_countries(
                countries,
                base_year=self._step_scope.get_base_year(),
                sim_years=self._step_scope.get_sim_years(),
            )
            self._step_macro.set_countries(
                countries,
                base_year=self._step_scope.get_base_year(),
                sim_years=self._step_scope.get_sim_years(),
            )

        elif idx == 1:
            # Data Acquisition → Projections & Build
            self._step_macro.fill_from_data(self._step_proxy.get_raw_macro_data())
            # Pre-load build inputs so Build button works
            nodes = self._step_scope.get_selected_nodes()
            sat_params = self._step_proxy.get_saturation_params()
            self._step_macro.set_build_inputs(
                nodes=nodes,
                resolution=self._step_scope.get_resolution(),
                national_demand_gwh=self._step_scope.get_national_demand_gwh(),
                proxy_results=self._step_proxy.get_proxy_results(),
                proxy_weights=self._step_proxy.get_proxy_weights(),
                meteo_data=self._step_proxy.get_meteo_data(),
                **sat_params,
            )

        elif idx == 2:
            # Projections → Calibration & Export
            result = self._step_macro.get_result()
            nodes = self._step_scope.get_selected_nodes()
            if result is not None:
                self._step_calib.set_result(
                    result=result,
                    nodes=nodes,
                    base_year=self._step_scope.get_base_year(),
                    sim_years=self._step_scope.get_sim_years(),
                )

    def _update_navigation(self) -> None:
        idx = self._current_step
        n = len(self._steps)
        c = current_theme().colors

        for i, lbl in enumerate(self._step_labels):
            if i == idx:
                lbl.setStyleSheet(
                    f"background-color: {c.accent_primary}; color: white; "
                    "border-radius: 4px; padding: 4px 8px; font-weight: bold;"
                )
            elif i < idx:
                lbl.setStyleSheet(
                    f"background-color: {c.accent_secondary}; color: white; "
                    "border-radius: 4px; padding: 4px 8px;"
                )
            else:
                lbl.setStyleSheet(
                    f"background-color: {c.surface_secondary}; "
                    f"color: {c.text_secondary}; "
                    "border-radius: 4px; padding: 4px 8px;"
                )

        self._btn_back.setEnabled(idx > 0)
        self._btn_back.setVisible(idx > 0)

        # Export button only on last step
        is_last = idx == n - 1
        self._btn_export.setVisible(is_last)
        self._export_status.setVisible(is_last)

        # Reconnect Next button
        try:
            self._btn_next.clicked.disconnect()
        except RuntimeError:
            pass

        if is_last:
            self._btn_next.setText(tr("common.close"))
            self._btn_next.clicked.connect(self.accept)
            self._btn_next.setEnabled(True)
        else:
            self._btn_next.setText(tr("wizard_demest.next"))
            self._btn_next.clicked.connect(self._go_next)
            self._btn_next.setEnabled(self._steps[idx].is_valid())

    # ──────────────────────────────────────────────────────────────────────────
    # Signal Handlers
    # ──────────────────────────────────────────────────────────────────────────

    def _refresh_next_enabled(self) -> None:
        """Re-evaluate whether the Next button should be enabled."""
        idx = self._current_step
        if idx < len(self._steps) - 1:
            self._btn_next.setEnabled(self._steps[idx].is_valid())

    def _on_export(self) -> None:
        """Delegate CSV export to the calibration step."""
        self._step_calib.export_csv()
        paths = self._step_calib.get_exported_paths()
        if paths:
            self._export_status.setText(
                tr("wizard_demest.exported_n_files").replace("{n}", str(len(paths)))
            )

    def _on_cancel(self) -> None:
        self._cleanup()
        self.reject()

    def accept(self) -> None:
        self._cleanup()
        super().accept()

    def reject(self) -> None:
        self._cleanup()
        super().reject()

    def closeEvent(self, event) -> None:
        self._cleanup()
        super().closeEvent(event)

    # ──────────────────────────────────────────────────────────────────────────
    # Cleanup
    # ──────────────────────────────────────────────────────────────────────────

    def _cleanup(self) -> None:
        """Cancel any running background workers and map interactions."""
        for step in self._steps:
            cancel = getattr(step, "cancel_all", None)
            if callable(cancel):
                cancel()
        if self._map_widget:
            try:
                self._map_widget.disable_domain_polygon_draw()
                self._map_widget.clear_domain_polygon()
            except Exception:
                pass
