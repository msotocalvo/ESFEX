# -*- coding: utf-8 -*-
"""OTEC Studio window shell (M0).

A non-modal workbench over a shared :class:`OtexProject`. M0 ships the shell:
a working scenario bar (new / branch / remove / compare) and a tabbed workbench
of placeholder panels. Panels are filled in across milestones M1–M7 (see
``OTEX_STUDIO_DESIGN.md``); each will read/write the active scenario.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.workflows.otec_studio.project import OtexProject

logger = logging.getLogger(__name__)


# Panel stubs: (tab title, one-line purpose, milestone). Replaced by real
# panels as each milestone lands; kept here so the shell is navigable and
# self-documenting from day one.
_PANELS = [
    ("Site & Resource",
     "Pick site(s); CMEMS/HYCOM data, multi-depth profiles, SSP climate "
     "scenario, siting hazard layers (MPA/AIS/seismic/cyclone).", "M6"),
    ("Cycle & Design",
     "Cycle & fluid with Kalina/Uehara composition and hybrid power-split; "
     "live T-s / P-h thermodynamic diagram per design point.", "M2"),
    ("Optimization ★",
     "Inverse design: optimize_site + UserConstraints to minimize LCOE, plus "
     "an evaluate() grid that draws the LCOE surface around the optimum.", "M1"),
    ("Operation",
     "Per-site otec_operation time-series with regulation-limit diagnostics "
     "(turbine vs condenser vs evaporator pinch).", "M4"),
    ("Economics",
     "CAPEX/OPEX breakdown, degradation models, NPV-LCOE, cost schemes, "
     "depth-cost tradeoff.", "M3"),
    ("Uncertainty & Sensitivity",
     "Monte Carlo / Sobol / Tornado over a selectable output metric with "
     "editable distributions, reused across scenarios.", "M5"),
    ("Regional ★",
     "Batch inverse-design across every site in a region "
     "(run_regional_optimization) with region-wide constraint what-ifs; "
     "LCOE site map + portfolio summary + CSV/HDF5 export.", "M7"),
]


def _stub_panel(title: str, purpose: str, milestone: str) -> QWidget:
    """A placeholder panel that documents what will live here."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setAlignment(Qt.AlignmentFlag.AlignTop)
    head = QLabel(f"<h2>{title}</h2>")
    lay.addWidget(head)
    desc = QLabel(purpose)
    desc.setWordWrap(True)
    desc.setStyleSheet("color: #aaa;")
    lay.addWidget(desc)
    badge = QLabel(f"Planned in milestone {milestone}")
    badge.setStyleSheet(
        "background-color: #34495e; color: #ecf0f1; border-radius: 4px; "
        "padding: 4px 8px; font-size: 11px;"
    )
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setMaximumWidth(220)
    lay.addSpacing(8)
    lay.addWidget(badge)
    lay.addStretch()
    return w


class OTECStudioWindow(QMainWindow):
    """Non-linear OTEC design/analysis workbench over the OTEX library.

    Additive sibling of ``OTECWizard`` — does not replace it. ``map_widget`` and
    ``model`` are accepted for future site-picking / data export but are
    optional; the Studio is self-contained in M0.
    """

    def __init__(self, parent=None, model=None, map_widget=None):
        super().__init__(parent)
        self.model = model
        self.map_widget = map_widget
        self.project = OtexProject()

        self.setWindowTitle("OTEC Studio")
        self.resize(1150, 780)
        # Independent top-level window (non-modal), not a child dialog.
        self.setWindowFlags(Qt.WindowType.Window)

        self._build_ui()
        self._refresh_scenarios()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addLayout(self._build_scenario_bar())

        self._tabs = QTabWidget()
        self._panels: dict[str, QWidget] = {}
        for title, purpose, milestone in _PANELS:
            panel = self._make_panel(title, purpose, milestone)
            self._panels[title] = panel
            self._tabs.addTab(panel, title)
        root.addWidget(self._tabs, 1)

    def _make_panel(self, title: str, purpose: str, milestone: str) -> QWidget:
        """Real panel where implemented (M1 Optimization, M2 Cycle), else stub."""
        if title.startswith("Optimization"):
            from esfex.visualization.workflows.otec_studio.optimization_panel import (
                OptimizationPanel,
            )
            return OptimizationPanel()
        if title.startswith("Cycle"):
            from esfex.visualization.workflows.otec_studio.cycle_panel import (
                CyclePanel,
            )
            return CyclePanel()
        if title.startswith("Economics"):
            from esfex.visualization.workflows.otec_studio.economics_panel import (
                EconomicsPanel,
            )
            return EconomicsPanel()
        if title.startswith("Operation"):
            from esfex.visualization.workflows.otec_studio.operation_panel import (
                OperationPanel,
            )
            return OperationPanel()
        if title.startswith("Uncertainty"):
            from esfex.visualization.workflows.otec_studio.uq_panel import (
                UncertaintyPanel,
            )
            return UncertaintyPanel()
        if title.startswith("Site"):
            from esfex.visualization.workflows.otec_studio.resource_panel import (
                ResourcePanel,
            )
            return ResourcePanel()
        if title.startswith("Regional"):
            from esfex.visualization.workflows.otec_studio.regional_panel import (
                RegionalPanel,
            )
            return RegionalPanel()
        return _stub_panel(title, purpose, milestone)

    def _build_scenario_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.addWidget(QLabel("<b>Scenario:</b>"))

        self._scenario_combo = QComboBox()
        self._scenario_combo.setMinimumWidth(220)
        self._scenario_combo.currentIndexChanged.connect(self._on_scenario_changed)
        bar.addWidget(self._scenario_combo)

        btn_new = QPushButton("New")
        btn_new.setToolTip("Start a fresh scenario with default config")
        btn_new.clicked.connect(self._on_new)
        bar.addWidget(btn_new)

        btn_branch = QPushButton("Branch")
        btn_branch.setToolTip(
            "Clone the active scenario's config (shares downloaded data) to "
            "vary one knob and compare"
        )
        btn_branch.clicked.connect(self._on_branch)
        bar.addWidget(btn_branch)

        self._btn_remove = QPushButton("Remove")
        self._btn_remove.clicked.connect(self._on_remove)
        bar.addWidget(self._btn_remove)

        bar.addStretch()

        btn_compare = QPushButton("Compare ☰")
        btn_compare.setToolTip("Side-by-side metrics across all scenarios")
        btn_compare.clicked.connect(self._on_compare)
        bar.addWidget(btn_compare)

        return bar

    # ------------------------------------------------------------------
    # Scenario bar behaviour
    # ------------------------------------------------------------------

    def _refresh_scenarios(self) -> None:
        """Repopulate the combo from the project without re-triggering signals."""
        self._scenario_combo.blockSignals(True)
        self._scenario_combo.clear()
        self._scenario_combo.addItems([s.name for s in self.project.scenarios])
        self._scenario_combo.setCurrentIndex(self.project.active_index)
        self._scenario_combo.blockSignals(False)
        self._btn_remove.setEnabled(len(self.project.scenarios) > 1)

    def _on_scenario_changed(self, index: int) -> None:
        if 0 <= index < len(self.project.scenarios):
            self.project.set_active(index)
            self._sync_panels()

    def _on_new(self) -> None:
        self.project.add_scenario()
        self._refresh_scenarios()
        self._sync_panels()

    def _on_branch(self) -> None:
        self.project.branch()
        self._refresh_scenarios()
        self._sync_panels()

    def _on_remove(self) -> None:
        if len(self.project.scenarios) <= 1:
            return
        self.project.remove_scenario(self.project.active_index)
        self._refresh_scenarios()
        self._sync_panels()

    def _sync_panels(self) -> None:
        """Notify panels that the active scenario changed (no-op for stubs)."""
        active = self.project.active
        for panel in self._panels.values():
            updater = getattr(panel, "on_scenario_changed", None)
            if callable(updater):
                updater(active, self.project)

    def _on_compare(self) -> None:
        ScenarioCompareDialog(self.project, parent=self).exec()


class ScenarioCompareDialog(QDialog):
    """Side-by-side metric table across all scenarios — the wizard's hard wall."""

    _COLUMNS = [
        ("name", "Scenario"),
        ("cycle", "Cycle"),
        ("fluid", "Fluid"),
        ("lcoe", "LCOE ($/kWh)"),
        ("p_net_mw", "Net Power (MW)"),
        ("capex", "CAPEX ($)"),
        ("has_results", "Has results"),
    ]

    def __init__(self, project: OtexProject, parent=None):
        super().__init__(parent)
        self._project = project
        self._rows = project.compare()
        self.setWindowTitle("Scenario Comparison")
        self.resize(820, 520)
        lay = QVBoxLayout(self)

        table = QTableWidget(len(self._rows), len(self._COLUMNS))
        table.setHorizontalHeaderLabels([c[1] for c in self._COLUMNS])
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        for r, row in enumerate(self._rows):
            for c, (key, _label) in enumerate(self._COLUMNS):
                val = row.get(key)
                if val is None:
                    text = "—"
                elif key == "lcoe" and isinstance(val, float):
                    text = f"{val:.4f}"
                elif key in ("p_net_mw", "capex") and isinstance(val, float):
                    text = f"{val:,.1f}"
                else:
                    text = str(val)
                table.setItem(r, c, QTableWidgetItem(text))
        lay.addWidget(table)

        # LCOE bar chart across scenarios (only those with a value)
        self._add_lcoe_chart(lay)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_export = QPushButton("Export CSV")
        btn_export.clicked.connect(self._export)
        btn_row.addWidget(btn_export)
        lay.addLayout(btn_row)

    def _add_lcoe_chart(self, lay):
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure

        named = [(r["name"], r["lcoe"]) for r in self._rows if r.get("lcoe")]
        if not named:
            return
        fig = Figure(figsize=(6, 2.6), dpi=100, layout="constrained")
        canvas = FigureCanvasQTAgg(fig)
        ax = fig.add_subplot(111)
        names = [n for n, _ in named]
        vals = [v for _, v in named]
        ax.bar(range(len(names)), vals, color="#2980b9", edgecolor="white")
        ax.set_xticks(range(len(names)))
        ax.set_xticklabels(names, rotation=20, ha="right", fontsize=8)
        ax.set_ylabel("LCOE")
        ax.set_title("LCOE by scenario", fontsize=10, fontweight="bold")
        ax.grid(axis="y", alpha=0.3)
        lay.addWidget(canvas)

    def _export(self):
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        path, _ = QFileDialog.getSaveFileName(
            self, "Export scenario comparison", "otec_scenarios.csv",
            "CSV (*.csv)")
        if not path:
            return
        try:
            import csv
            keys = [k for k, _ in self._COLUMNS]
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                for row in self._rows:
                    w.writerow({k: row.get(k) for k in keys})
            QMessageBox.information(self, "Export", f"Saved to {path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(exc))
