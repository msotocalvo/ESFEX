"""Investment portfolio entry form with per-node table."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import (
    GuiInvestmentNodeData,
    GuiModel,
)
from esfex.visualization.i18n import tr
from esfex.visualization.panels.word_wrap_header import WordWrapHeaderView

# Technology types that can be invested in
_TECHNOLOGY_TYPE_KEYS = [
    ("generator", "investment_form.tech_generator"),
    ("battery", "investment_form.tech_battery"),
    ("electrolyzer", "investment_form.tech_electrolyzer"),
    ("acdc_converter", "investment_form.tech_acdc"),
    ("freq_converter", "investment_form.tech_freq"),
    ("transmission", "investment_form.tech_transmission"),
    ("fuel_storage", "investment_form.tech_fuel_storage"),
]


class InvestmentForm(QWidget):
    """Property editor for an investment portfolio entry."""

    investmentChanged = Signal(str)       # entry_id
    investmentDeleteRequested = Signal(str)

    def __init__(self, model: GuiModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._current_id: str | None = None
        self._multi_ids: list[str] | None = None
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        self._system_label = QLabel("")
        self._system_label.setObjectName("headerLabel")
        layout.addWidget(self._system_label)

        # --- Identity ---
        grp_id = QGroupBox(tr("investment_form.group_identity"))
        fl = QFormLayout(grp_id)
        fl.setContentsMargins(6, 6, 6, 6)
        fl.setSpacing(4)

        self._name = QLineEdit()
        self._name.editingFinished.connect(self._on_header_changed)
        fl.addRow(tr("generator_form.name"), self._name)

        self._tech_type = QComboBox()
        for key, tr_key in _TECHNOLOGY_TYPE_KEYS:
            self._tech_type.addItem(tr(tr_key), key)
        self._tech_type.currentIndexChanged.connect(self._on_tech_type_changed)
        fl.addRow(tr("investment_form.type"), self._tech_type)

        self._tech_ref = QComboBox()
        self._tech_ref.addItem(tr("investment_form.none_tech"), "")
        self._tech_ref.currentIndexChanged.connect(self._on_header_changed)
        fl.addRow(tr("generator_form.technology"), self._tech_ref)

        layout.addWidget(grp_id)

        # --- Per-node investment table ---
        grp_table = QGroupBox(tr("investment_form.group_per_node"))
        tbl_layout = QVBoxLayout(grp_table)
        tbl_layout.setContentsMargins(6, 6, 6, 6)

        self._table = QTableWidget()
        self._table.setHorizontalHeader(WordWrapHeaderView(self._table))
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.cellChanged.connect(self._on_table_cell_changed)
        tbl_layout.addWidget(self._table)

        # Auto-populate button
        btn_row = QHBoxLayout()
        self._populate_btn = QPushButton(tr("investment_form.populate_btn"))
        self._populate_btn.setToolTip(tr("investment_form.populate_tip"))
        self._populate_btn.clicked.connect(self._on_populate_from_nodes)
        btn_row.addWidget(self._populate_btn)
        btn_row.addStretch()
        tbl_layout.addLayout(btn_row)

        layout.addWidget(grp_table)

        # --- Delete button ---
        self._delete_btn = QPushButton(tr("investment_form.delete_entry"))
        self._delete_btn.setObjectName("deleteButton")
        self._delete_btn.clicked.connect(self._on_delete)
        layout.addWidget(self._delete_btn)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def _field_map(self):
        return [
            ("name", self._name),
            ("technology_type", self._tech_type),
        ]

    def load_elements(self, element_ids: list[str]):
        """Load multiple investment entries for batch editing (header fields only)."""
        from esfex.visualization.panels.multi_edit import collect_attr, set_widget_value

        portfolio = self._model.state.investment_portfolio
        instances = [portfolio[eid] for eid in element_ids if eid in portfolio]
        if not instances:
            return
        self._multi_ids = element_ids
        self._current_id = element_ids[0]
        self._system_label.setText(tr("investment_form.n_selected", n=len(element_ids)))
        self._updating = True

        for attr, w in self._field_map():
            set_widget_value(w, collect_attr(instances, attr))

        # Clear the per-node table (not meaningful for multi-edit)
        self._table.setRowCount(0)
        self._populate_btn.setEnabled(False)

        self._updating = False

    def load_element(self, entry_id: str):
        self._multi_ids = None
        self._populate_btn.setEnabled(True)
        entry = self._model.state.investment_portfolio.get(entry_id)
        if not entry:
            return
        self._current_id = entry_id
        self._updating = True

        self._system_label.setText(tr("investment_form.system_label", name=self._model.state.name))
        self._name.setText(entry.name)

        # Technology type
        idx = self._tech_type.findData(entry.technology_type)
        if idx >= 0:
            self._tech_type.setCurrentIndex(idx)

        # Technology reference combo
        self._tech_ref.blockSignals(True)
        self._tech_ref.clear()
        self._tech_ref.addItem(tr("investment_form.none_tech"), "")
        for tech in self._model.state.technologies.values():
            self._tech_ref.addItem(tech.name, tech.tech_id)
        tech_idx = self._tech_ref.findData(entry.technology_id or "")
        if tech_idx >= 0:
            self._tech_ref.setCurrentIndex(tech_idx)
        self._tech_ref.blockSignals(False)

        # Build table
        self._rebuild_table(entry)

        self._updating = False

    # ------------------------------------------------------------------
    # Table management
    # ------------------------------------------------------------------

    def _rebuild_table(self, entry=None):
        """Rebuild the table from the entry's node_data."""
        if entry is None and self._current_id:
            entry = self._model.state.investment_portfolio.get(self._current_id)
        if not entry:
            return

        self._updating = True
        is_battery = entry.technology_type == "battery"

        # Columns
        if is_battery:
            headers = [
                tr("investment_form.node_col"), tr("investment_form.invest_cost_col"),
                tr("investment_form.invest_max_col"), tr("investment_form.energy_cost_col"),
                tr("investment_form.energy_max_col"),
            ]
        else:
            headers = [tr("investment_form.node_col"), tr("investment_form.invest_cost_col"),
                       tr("investment_form.invest_max_col")]

        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setRowCount(len(entry.node_data))

        for row, nd in enumerate(entry.node_data):
            # Node name (read-only)
            node_name = f"Node {nd.node_index}"
            for n in self._model.state.nodes:
                if n.index == nd.node_index:
                    node_name = n.name or node_name
                    break
            name_item = QTableWidgetItem(node_name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            name_item.setData(Qt.ItemDataRole.UserRole, nd.node_index)
            self._table.setItem(row, 0, name_item)

            # Invest cost
            self._set_float_cell(row, 1, nd.invest_cost)
            # Invest max
            self._set_float_cell(row, 2, nd.invest_max)

            if is_battery:
                # Energy cost
                self._set_float_cell(
                    row, 3, entry.invest_cost_energy.get(nd.node_index, 0.0)
                )
                # Energy max capacity
                self._set_float_cell(
                    row, 4, entry.invest_max_capacity.get(nd.node_index, 0.0)
                )

        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for c in range(1, len(headers)):
            self._table.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeMode.ResizeToContents
            )
        self._updating = False

    def _set_float_cell(self, row: int, col: int, value: float):
        item = QTableWidgetItem(f"{value:.2f}")
        item.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._table.setItem(row, col, item)

    def _on_populate_from_nodes(self):
        """Add rows for all system nodes not already in the table."""
        if not self._current_id:
            return
        entry = self._model.state.investment_portfolio.get(self._current_id)
        if not entry:
            return

        existing = {nd.node_index for nd in entry.node_data}
        for node in self._model.state.nodes:
            if node.index not in existing:
                entry.node_data.append(
                    GuiInvestmentNodeData(node_index=node.index)
                )

        self._rebuild_table(entry)
        self._emit_change()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_tech_type_changed(self, _idx: int):
        if self._updating or not self._current_id:
            return
        from esfex.visualization.panels.multi_edit import widget_is_mixed
        if widget_is_mixed(self._tech_type):
            return

        ids = self._multi_ids or ([self._current_id] if self._current_id else [])
        portfolio = self._model.state.investment_portfolio
        new_type = self._tech_type.currentData()
        for eid in ids:
            entry = portfolio.get(eid)
            if entry and new_type != entry.technology_type:
                entry.technology_type = new_type
        if not self._multi_ids:
            entry = portfolio.get(self._current_id)
            if entry:
                self._rebuild_table(entry)
        for eid in ids:
            self._model.investmentEntryUpdated.emit(eid)
            self.investmentChanged.emit(eid)

    def _on_header_changed(self):
        if self._updating or not self._current_id:
            return
        from esfex.visualization.panels.multi_edit import widget_is_mixed

        ids = self._multi_ids or ([self._current_id] if self._current_id else [])
        portfolio = self._model.state.investment_portfolio
        for eid in ids:
            entry = portfolio.get(eid)
            if not entry:
                continue
            if not widget_is_mixed(self._name):
                entry.name = self._name.text()
            if not widget_is_mixed(self._tech_ref):
                entry.technology_id = self._tech_ref.currentData() or ""
        for eid in ids:
            self._model.investmentEntryUpdated.emit(eid)
            self.investmentChanged.emit(eid)

    def _on_table_cell_changed(self, row: int, col: int):
        if self._updating or not self._current_id:
            return
        entry = self._model.state.investment_portfolio.get(self._current_id)
        if not entry or row >= len(entry.node_data):
            return

        item = self._table.item(row, col)
        if not item:
            return
        try:
            val = float(item.text())
        except ValueError:
            return

        nd = entry.node_data[row]
        if col == 1:
            nd.invest_cost = val
        elif col == 2:
            nd.invest_max = val
        elif col == 3 and entry.technology_type == "battery":
            entry.invest_cost_energy[nd.node_index] = val
        elif col == 4 and entry.technology_type == "battery":
            entry.invest_max_capacity[nd.node_index] = val

        self._emit_change()

    def _on_delete(self):
        if not self._current_id:
            return
        from esfex.visualization.panels._dialogs import confirm_delete
        if confirm_delete(
            self,
            tr("investment_form.confirm_delete_title"),
            tr("investment_form.confirm_delete_msg"),
        ):
            self.investmentDeleteRequested.emit(self._current_id)

    def _emit_change(self):
        if self._current_id:
            self._model.investmentEntryUpdated.emit(self._current_id)
            self.investmentChanged.emit(self._current_id)
