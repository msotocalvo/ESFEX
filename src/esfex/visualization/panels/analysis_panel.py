"""Hypothetical dispatch scenario panel for real-time SLD analysis.

Provides editable tables for generator output/status and node demand,
emitting a ``scenarioChanged`` signal (debounced) whenever the user
modifies any value.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import GuiModel
from esfex.visualization.i18n import tr


class AnalysisPanel(QWidget):
    """Editable dispatch scenario for real-time frequency/contingency analysis.

    Signals
    -------
    scenarioChanged
        Emitted (debounced 300 ms) when any dispatch value changes.
    """

    scenarioChanged = Signal()
    analysisModeChanged = Signal(str)  # "dc", "ac", or "sc"
    runAllN1Requested = Signal(str, str, float)  # depth, redispatch, pi_threshold
    runScreeningRequested = Signal(str, float)  # redispatch, pi_threshold

    def __init__(self, model: GuiModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self._updating = False

        # Debounce timer
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self.scenarioChanged.emit)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        # Title
        self._title_label = QLabel(tr("analysis_panel.title"))
        self._title_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        layout.addWidget(self._title_label)

        # ── Analysis mode selector ──
        mode_row = QHBoxLayout()
        mode_row.setSpacing(4)
        self._mode_label = QLabel(tr("analysis_panel.mode_label"))
        mode_row.addWidget(self._mode_label)
        self._mode_combo = QComboBox()
        self._mode_combo.addItem(tr("analysis_panel.mode_dc"), "dc")
        # AC/SC modes added dynamically if pandapower available
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self._mode_combo)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # ── Power flow status ──
        self._pf_status_label = QLabel()
        self._pf_status_label.setVisible(False)
        layout.addWidget(self._pf_status_label)

        # ── Generator dispatch table ──
        self._grp_gen = QGroupBox(tr("analysis_panel.generators"))
        grp_gen = self._grp_gen
        gen_layout = QVBoxLayout(grp_gen)
        gen_layout.setContentsMargins(4, 4, 4, 4)
        gen_layout.setSpacing(2)
        self._gen_table = QTableWidget()
        self._gen_table.setColumnCount(4)
        self._gen_table.setHorizontalHeaderLabels([
            tr("analysis_panel.col_generator"),
            tr("analysis_panel.col_rated"),
            tr("analysis_panel.col_output"),
            tr("analysis_panel.col_on"),
        ])
        self._gen_table.horizontalHeader().setStretchLastSection(False)
        self._gen_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch,
        )
        self._gen_table.verticalHeader().setVisible(False)
        self._gen_table.setAlternatingRowColors(True)
        # Compact row height for denser display (20 rows visible by default)
        self._gen_table.verticalHeader().setDefaultSectionSize(24)
        self._gen_table.setMinimumHeight(20 * 24 + self._gen_table.horizontalHeader().height())
        gen_layout.addWidget(self._gen_table, 1)  # stretch factor 1

        # Quick-set buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(2)
        self._btn_all_on = QPushButton(tr("analysis_panel.all_on"))
        self._btn_all_on.clicked.connect(self._set_all_on)
        btn_row.addWidget(self._btn_all_on)
        self._btn_rated = QPushButton(tr("analysis_panel.set_rated"))
        self._btn_rated.clicked.connect(self._set_all_rated)
        btn_row.addWidget(self._btn_rated)
        self._btn_80pct = QPushButton(tr("analysis_panel.set_80pct"))
        self._btn_80pct.clicked.connect(self._set_all_80pct)
        btn_row.addWidget(self._btn_80pct)
        btn_row.addStretch()
        gen_layout.addLayout(btn_row)
        layout.addWidget(grp_gen, 3)  # stretch factor 3 — gen table gets most space

        # ── Demand table ──
        self._grp_demand = QGroupBox(tr("analysis_panel.demand"))
        grp_demand = self._grp_demand
        demand_layout = QVBoxLayout(grp_demand)
        demand_layout.setContentsMargins(4, 4, 4, 4)
        demand_layout.setSpacing(2)
        self._demand_table = QTableWidget()
        self._demand_table.setColumnCount(2)
        self._demand_table.setHorizontalHeaderLabels([
            tr("analysis_panel.col_node"),
            tr("analysis_panel.col_demand"),
        ])
        self._demand_table.horizontalHeader().setStretchLastSection(True)
        self._demand_table.verticalHeader().setVisible(False)
        self._demand_table.setAlternatingRowColors(True)
        self._demand_table.verticalHeader().setDefaultSectionSize(24)
        self._demand_table.setMinimumHeight(8 * 24 + self._demand_table.horizontalHeader().height())
        demand_layout.addWidget(self._demand_table, 1)

        # Balance button
        bal_row = QHBoxLayout()
        self._btn_balance = QPushButton(tr("analysis_panel.balance_demand"))
        self._btn_balance.clicked.connect(self._balance_demand)
        bal_row.addWidget(self._btn_balance)
        bal_row.addStretch()
        demand_layout.addLayout(bal_row)
        layout.addWidget(grp_demand, 1)  # stretch factor 1 — demand table smaller

        # ── N-k Analysis Options ──
        self._grp_nk = QGroupBox(tr("analysis_panel.nk_group"))
        nk_layout = QVBoxLayout(self._grp_nk)
        nk_layout.setContentsMargins(4, 4, 4, 4)
        nk_layout.setSpacing(3)

        # Row 1: Contingency depth selector
        nk_row1 = QHBoxLayout()
        nk_row1.setSpacing(4)
        nk_row1.addWidget(QLabel(tr("analysis_panel.nk_depth")))
        self._nk_depth_combo = QComboBox()
        self._nk_depth_combo.addItem("N-1", "n1")
        self._nk_depth_combo.addItem("N-1-1", "n1_1")
        nk_row1.addWidget(self._nk_depth_combo)
        nk_row1.addStretch()
        nk_layout.addLayout(nk_row1)

        # Row 2: Redistribution mode
        nk_row2 = QHBoxLayout()
        nk_row2.setSpacing(4)
        nk_row2.addWidget(QLabel(tr("analysis_panel.nk_redispatch")))
        self._nk_redispatch_combo = QComboBox()
        self._nk_redispatch_combo.addItem(tr("analysis_panel.nk_prorata"), "pro_rata")
        self._nk_redispatch_combo.addItem(tr("analysis_panel.nk_droop"), "droop")
        nk_row2.addWidget(self._nk_redispatch_combo)
        nk_row2.addStretch()
        nk_layout.addLayout(nk_row2)

        # Row 3: PI screening threshold
        nk_row3 = QHBoxLayout()
        nk_row3.setSpacing(4)
        nk_row3.addWidget(QLabel(tr("analysis_panel.nk_pi_threshold")))
        self._nk_pi_threshold = QDoubleSpinBox()
        self._nk_pi_threshold.setRange(0, 100)
        self._nk_pi_threshold.setDecimals(2)
        self._nk_pi_threshold.setValue(0.0)
        self._nk_pi_threshold.setToolTip(
            tr("analysis_panel.nk_pi_tip"))
        nk_row3.addWidget(self._nk_pi_threshold)
        nk_row3.addStretch()
        nk_layout.addLayout(nk_row3)

        # Row 4: Run All N-1 button + summary
        nk_row4 = QHBoxLayout()
        nk_row4.setSpacing(4)
        self._btn_run_all_n1 = QPushButton(tr("analysis_panel.nk_run_all"))
        self._btn_run_all_n1.clicked.connect(self._on_run_all_n1)
        nk_row4.addWidget(self._btn_run_all_n1)
        self._btn_run_screening = QPushButton(tr("analysis_panel.nk_screen"))
        self._btn_run_screening.clicked.connect(self._on_run_screening)
        nk_row4.addWidget(self._btn_run_screening)
        nk_row4.addStretch()
        nk_layout.addLayout(nk_row4)

        # N-1 summary results area
        self._nk_summary_label = QLabel()
        self._nk_summary_label.setWordWrap(True)
        self._nk_summary_label.setStyleSheet("font-size: 10px; padding: 2px;")
        nk_layout.addWidget(self._nk_summary_label)

        layout.addWidget(self._grp_nk, 0)  # no stretch — stays compact

        # ── Summary label ──
        self._summary_label = QLabel()
        layout.addWidget(self._summary_label)

        # Spin/checkbox widget storage
        self._gen_spins: dict[str, QDoubleSpinBox] = {}
        self._gen_checks: dict[str, QCheckBox] = {}
        self._demand_spins: dict[int, QDoubleSpinBox] = {}
        self._gen_ids_order: list[str] = []

    # ------------------------------------------------------------------
    # Retranslation
    # ------------------------------------------------------------------

    def retranslateUi(self):
        """Update translatable strings."""
        self._title_label.setText(tr("analysis_panel.title"))
        self._mode_label.setText(tr("analysis_panel.mode_label"))
        self._grp_gen.setTitle(tr("analysis_panel.generators"))
        self._gen_table.setHorizontalHeaderLabels([
            tr("analysis_panel.col_generator"),
            tr("analysis_panel.col_rated"),
            tr("analysis_panel.col_output"),
            tr("analysis_panel.col_on"),
        ])
        self._btn_all_on.setText(tr("analysis_panel.all_on"))
        self._btn_rated.setText(tr("analysis_panel.set_rated"))
        self._btn_80pct.setText(tr("analysis_panel.set_80pct"))
        self._grp_demand.setTitle(tr("analysis_panel.demand"))
        self._demand_table.setHorizontalHeaderLabels([
            tr("analysis_panel.col_node"),
            tr("analysis_panel.col_demand"),
        ])
        self._btn_balance.setText(tr("analysis_panel.balance_demand"))
        # Mode combo items
        for i in range(self._mode_combo.count()):
            data = self._mode_combo.itemData(i)
            if data == "dc":
                self._mode_combo.setItemText(i, tr("analysis_panel.mode_dc"))
            elif data == "ac":
                self._mode_combo.setItemText(i, tr("analysis_panel.mode_ac"))
            elif data == "sc":
                self._mode_combo.setItemText(i, tr("analysis_panel.mode_sc"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def populate(self) -> None:
        """Populate tables from the current GuiSystemState."""
        self._updating = True
        state = self._model.state

        # ── Generator table ──
        gens = list(state.generators.items())
        self._gen_table.setRowCount(len(gens))
        self._gen_spins.clear()
        self._gen_checks.clear()
        self._gen_ids_order = []

        for row, (gen_id, gen) in enumerate(gens):
            self._gen_ids_order.append(gen_id)

            # Name
            name_item = QTableWidgetItem(gen.name or gen_id)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._gen_table.setItem(row, 0, name_item)

            # Rated power (read-only)
            rated_item = QTableWidgetItem(f"{gen.rated_power:.1f}")
            rated_item.setFlags(rated_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._gen_table.setItem(row, 1, rated_item)

            # Output (editable spinbox)
            spin = QDoubleSpinBox()
            spin.setRange(0, gen.rated_power)
            spin.setDecimals(3)
            spin.setSuffix(" MW")
            is_re = gen.gen_type.lower() == "renewable"
            spin.setValue(gen.rated_power * (0.5 if is_re else 0.8))
            spin.valueChanged.connect(self._on_value_changed)
            self._gen_table.setCellWidget(row, 2, spin)
            self._gen_spins[gen_id] = spin

            # On/Off checkbox
            check = QCheckBox()
            check.setChecked(True)
            check.stateChanged.connect(self._on_value_changed)
            container = QWidget()
            cl = QHBoxLayout(container)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.addStretch()
            cl.addWidget(check)
            cl.addStretch()
            self._gen_table.setCellWidget(row, 3, container)
            self._gen_checks[gen_id] = check

        self._gen_table.resizeColumnsToContents()

        # ── Demand table ──
        num_nodes = len(state.nodes)
        self._demand_table.setRowCount(num_nodes)
        self._demand_spins.clear()

        for ni in range(num_nodes):
            nd = state.nodes[ni]
            label = getattr(nd, "name", f"Node {ni}")
            node_item = QTableWidgetItem(label)
            node_item.setFlags(node_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._demand_table.setItem(ni, 0, node_item)

            # Sum generation at this node for default demand
            node_gen = sum(
                self._gen_spins[gid].value()
                for gid, gen in state.generators.items()
                if self._bus_to_node(gen.bus) == ni
                and self._gen_checks[gid].isChecked()
            )

            spin = QDoubleSpinBox()
            spin.setRange(0, 1e6)
            spin.setDecimals(3)
            spin.setSuffix(" MW")
            spin.setValue(node_gen)
            spin.valueChanged.connect(self._on_value_changed)
            self._demand_table.setCellWidget(ni, 1, spin)
            self._demand_spins[ni] = spin

        self._demand_table.resizeColumnsToContents()
        self._updating = False
        self._update_summary()

    def get_scenario(self):
        """Return the current HypotheticalScenario from table values."""
        from esfex.analysis.snapshot_builder import HypotheticalScenario

        gen_outputs: dict[str, float] = {}
        gen_status: dict[str, bool] = {}
        for gen_id, spin in self._gen_spins.items():
            gen_outputs[gen_id] = spin.value()
            gen_status[gen_id] = self._gen_checks[gen_id].isChecked()

        node_demands: dict[int, float] = {}
        for ni, spin in self._demand_spins.items():
            node_demands[ni] = spin.value()

        return HypotheticalScenario(
            gen_outputs=gen_outputs,
            gen_status=gen_status,
            node_demands=node_demands,
        )

    def get_analysis_mode(self) -> str:
        """Return the current analysis mode: 'dc', 'ac', or 'sc'."""
        return self._mode_combo.currentData() or "dc"

    def setup_modes(
        self,
        ac_available: bool,
        sc_available: bool | None = None,
    ) -> None:
        """Configure available analysis modes.

        Parameters
        ----------
        ac_available : bool
            Whether AC power flow is available (native Julia or pandapower).
        sc_available : bool | None
            Whether IEC 60909 short-circuit is available (pandapower only).
            Defaults to ``ac_available`` for backward compatibility.
        """
        if sc_available is None:
            sc_available = ac_available
        self._mode_combo.blockSignals(True)
        # Remove AC/SC items if they exist (indices 1, 2)
        while self._mode_combo.count() > 1:
            self._mode_combo.removeItem(1)
        if ac_available:
            self._mode_combo.addItem(tr("analysis_panel.mode_ac"), "ac")
        if sc_available:
            self._mode_combo.addItem(tr("analysis_panel.mode_sc"), "sc")
        self._mode_combo.blockSignals(False)

    def update_pf_status(
        self,
        converged: bool,
        iterations: int = 0,
        violations: int = 0,
        losses_mw: float = 0.0,
    ) -> None:
        """Update the power flow convergence status label."""
        if converged:
            text = (
                f"AC PF: Converged ({iterations} iter) | "
                f"Losses: {losses_mw:.2f} MW"
            )
            if violations > 0:
                text += f" | V violations: {violations}"
                color = "#e67e22"  # orange
            else:
                color = "#27ae60"  # green
        else:
            text = "AC PF: DIVERGED"
            color = "#e74c3c"  # red

        self._pf_status_label.setText(text)
        self._pf_status_label.setStyleSheet(
            f"color: {color}; font-weight: bold; font-size: 11px; padding: 2px;"
        )
        self._pf_status_label.setVisible(True)

    def hide_pf_status(self) -> None:
        """Hide the power flow status label (for DC mode)."""
        self._pf_status_label.setVisible(False)

    # ------------------------------------------------------------------
    # Quick-set actions
    # ------------------------------------------------------------------

    def _set_all_on(self) -> None:
        self._updating = True
        for check in self._gen_checks.values():
            check.setChecked(True)
        self._updating = False
        self._on_value_changed()

    def _set_all_rated(self) -> None:
        self._updating = True
        state = self._model.state
        for gen_id, spin in self._gen_spins.items():
            gen = state.generators[gen_id]
            spin.setValue(gen.rated_power)
        self._updating = False
        self._on_value_changed()

    def _set_all_80pct(self) -> None:
        self._updating = True
        state = self._model.state
        for gen_id, spin in self._gen_spins.items():
            gen = state.generators[gen_id]
            spin.setValue(gen.rated_power * 0.8)
        self._updating = False
        self._on_value_changed()

    def _balance_demand(self) -> None:
        """Set each node's demand equal to its generation."""
        self._updating = True
        state = self._model.state
        for ni, spin in self._demand_spins.items():
            node_gen = sum(
                self._gen_spins[gid].value()
                for gid, gen in state.generators.items()
                if self._bus_to_node(gen.bus) == ni
                and self._gen_checks[gid].isChecked()
            )
            spin.setValue(node_gen)
        self._updating = False
        self._on_value_changed()

    def _on_run_all_n1(self) -> None:
        """Emit signal to run full N-1 (or N-1-1) analysis."""
        depth = self._nk_depth_combo.currentData() or "n1"
        redispatch = self._nk_redispatch_combo.currentData() or "pro_rata"
        pi_thresh = self._nk_pi_threshold.value()
        self.runAllN1Requested.emit(depth, redispatch, pi_thresh)

    def _on_run_screening(self) -> None:
        """Emit signal to run PI-based contingency screening."""
        redispatch = self._nk_redispatch_combo.currentData() or "pro_rata"
        pi_thresh = self._nk_pi_threshold.value()
        self.runScreeningRequested.emit(redispatch, pi_thresh)

    def update_nk_summary(self, text: str) -> None:
        """Update the N-k summary label with results."""
        self._nk_summary_label.setText(text)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_mode_changed(self, _index: int) -> None:
        mode = self._mode_combo.currentData() or "dc"
        if mode == "dc":
            self.hide_pf_status()
        self.analysisModeChanged.emit(mode)

    def _on_value_changed(self, _=None) -> None:
        if self._updating:
            return
        self._update_summary()
        self._debounce.start()

    def _update_summary(self) -> None:
        total_gen = sum(
            spin.value()
            for gid, spin in self._gen_spins.items()
            if self._gen_checks[gid].isChecked()
        )
        total_demand = sum(spin.value() for spin in self._demand_spins.values())
        balance = total_gen - total_demand
        sign = "+" if balance >= 0 else ""
        self._summary_label.setText(
            f"Gen: {total_gen:.1f} MW | Demand: {total_demand:.1f} MW | "
            f"Balance: {sign}{balance:.1f} MW"
        )

    def _bus_to_node(self, bus_id: str) -> int:
        bus = self._model.state.buses.get(bus_id)
        if bus is not None:
            return bus.parent_node
        try:
            return int(bus_id.split("_")[-1])
        except (ValueError, IndexError):
            return 0
