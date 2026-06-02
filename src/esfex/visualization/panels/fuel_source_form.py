"""Fuel source (primary energy) properties form."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import GuiModel
from esfex.visualization.i18n import tr
from esfex.visualization.panels.word_wrap_header import WordWrapHeaderView


class FuelSourceForm(QWidget):
    """Property editor for a primary energy source."""

    fuelSourceChanged = Signal(str)

    def __init__(self, model: GuiModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._current_id: str | None = None
        self._multi_ids: list[str] | None = None
        self._updating = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._system_label = QLabel("")
        self._system_label.setObjectName("headerLabel")
        outer.addWidget(self._system_label)

        # --- Basic Properties ---
        grp_basic = QGroupBox(tr("fuel_source_form.group_basic"))
        fl = QFormLayout(grp_basic)

        self._name = QLineEdit()
        self._name.editingFinished.connect(self._on_changed)
        fl.addRow(tr("generator_form.name"), self._name)

        self._unit = QLineEdit()
        self._unit.editingFinished.connect(self._on_changed)
        fl.addRow(tr("fuel_source_form.unit"), self._unit)

        outer.addWidget(grp_basic)

        # --- System-level scalars ---
        grp_sys = QGroupBox(tr("fuel_source_form.group_system"))
        fl_sys = QFormLayout(grp_sys)

        self._min_storage_level = QDoubleSpinBox()
        self._min_storage_level.setRange(0, 1)
        self._min_storage_level.setDecimals(2)
        self._min_storage_level.setSingleStep(0.05)
        self._min_storage_level.editingFinished.connect(self._on_changed)
        fl_sys.addRow(tr("fuel_source_form.min_storage_level"), self._min_storage_level)

        self._storage_invest_cost = QDoubleSpinBox()
        self._storage_invest_cost.setRange(0, 1e9)
        self._storage_invest_cost.setDecimals(2)
        self._storage_invest_cost.editingFinished.connect(self._on_changed)
        fl_sys.addRow(tr("fuel_source_form.storage_invest_cost"), self._storage_invest_cost)

        self._transport_cost = QDoubleSpinBox()
        self._transport_cost.setRange(0, 1e9)
        self._transport_cost.setDecimals(4)
        self._transport_cost.editingFinished.connect(self._on_changed)
        fl_sys.addRow(tr("fuel_source_form.transport_cost"), self._transport_cost)

        self._transport_losses = QDoubleSpinBox()
        self._transport_losses.setRange(0, 1)
        self._transport_losses.setDecimals(4)
        self._transport_losses.editingFinished.connect(self._on_changed)
        fl_sys.addRow(tr("fuel_source_form.transport_losses"), self._transport_losses)

        self._max_storage_inv = QDoubleSpinBox()
        self._max_storage_inv.setRange(0, 1e9)
        self._max_storage_inv.setDecimals(2)
        self._max_storage_inv.editingFinished.connect(self._on_changed)
        fl_sys.addRow(tr("fuel_source_form.max_storage_inv"), self._max_storage_inv)

        self._max_transport_inv = QDoubleSpinBox()
        self._max_transport_inv.setRange(0, 1e9)
        self._max_transport_inv.setDecimals(2)
        self._max_transport_inv.editingFinished.connect(self._on_changed)
        fl_sys.addRow(tr("fuel_source_form.max_transport_inv"), self._max_transport_inv)

        outer.addWidget(grp_sys)

        # --- Per-node table ---
        grp_nodes = QGroupBox(tr("fuel_source_form.group_per_node"))
        fl_nodes = QVBoxLayout(grp_nodes)

        self._node_table = QTableWidget()
        self._node_table.setHorizontalHeader(WordWrapHeaderView(self._node_table))
        self._node_table.setColumnCount(4)
        self._node_table.setHorizontalHeaderLabels([
            tr("fuel_source_form.max_avail"), tr("fuel_source_form.import_cost"),
            tr("fuel_source_form.storage_cap"), tr("fuel_source_form.init_storage")
        ])
        self._node_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._node_table.cellChanged.connect(self._on_table_changed)
        fl_nodes.addWidget(self._node_table)

        outer.addWidget(grp_nodes)

        outer.addStretch()

    def _field_map(self):
        return [
            ("name", self._name),
            ("unit", self._unit),
            ("min_storage_level", self._min_storage_level),
            ("storage_investment_cost", self._storage_invest_cost),
            ("transport_cost", self._transport_cost),
            ("transport_losses", self._transport_losses),
            ("max_storage_investment_per_node", self._max_storage_inv),
            ("max_transport_investment_per_arc", self._max_transport_inv),
        ]

    def load_elements(self, element_ids: list[str]):
        """Load multiple fuel sources for batch editing (scalar fields only)."""
        from esfex.visualization.panels.multi_edit import collect_attr, set_widget_value

        sources = self._model.state.fuel_sources
        instances = [sources[eid] for eid in element_ids if eid in sources]
        if not instances:
            return
        self._multi_ids = element_ids
        self._current_id = element_ids[0]
        self._system_label.setText(tr("fuel_source_form.n_selected", n=len(element_ids)))
        self._updating = True

        for attr, w in self._field_map():
            set_widget_value(w, collect_attr(instances, attr))

        # Clear per-node table (not meaningful for multi-edit)
        self._node_table.setRowCount(0)

        self._updating = False

    def load_element(self, element_id: str):
        self._multi_ids = None
        self._current_id = element_id
        source = self._model.state.fuel_sources.get(element_id)
        if not source:
            return

        self._system_label.setText(tr("fuel_source_form.system_label", name=self._model.state.name))
        self._updating = True

        self._name.setText(source.name)
        self._unit.setText(source.unit)
        self._min_storage_level.setValue(source.min_storage_level)
        self._storage_invest_cost.setValue(source.storage_investment_cost)
        self._transport_cost.setValue(source.transport_cost)
        self._transport_losses.setValue(source.transport_losses)
        self._max_storage_inv.setValue(source.max_storage_investment_per_node)
        self._max_transport_inv.setValue(source.max_transport_investment_per_arc)

        # Populate per-node table
        num_nodes = len(self._model.state.nodes)
        # Ensure arrays are long enough
        while len(source.max_availability) < num_nodes:
            source.max_availability.append(0.0)
            source.import_cost.append(0.0)
            source.storage_capacity.append(0.0)
            source.initial_storage_level.append(0.5)

        self._node_table.setRowCount(num_nodes)
        for i in range(num_nodes):
            node = self._model.state.nodes[i]
            self._node_table.setVerticalHeaderItem(
                i, QTableWidgetItem(node.name)
            )
            self._node_table.setItem(
                i, 0, QTableWidgetItem(f"{source.max_availability[i]:.2f}")
            )
            self._node_table.setItem(
                i, 1, QTableWidgetItem(f"{source.import_cost[i]:.2f}")
            )
            self._node_table.setItem(
                i, 2, QTableWidgetItem(f"{source.storage_capacity[i]:.2f}")
            )
            self._node_table.setItem(
                i, 3, QTableWidgetItem(f"{source.initial_storage_level[i]:.2f}")
            )

        self._updating = False

    def _on_changed(self):
        if self._updating or not self._current_id:
            return
        self._model.checkpoint()
        from esfex.visualization.panels.multi_edit import widget_is_mixed

        ids = self._multi_ids or ([self._current_id] if self._current_id else [])
        kwargs: dict = {}
        for attr, w in self._field_map():
            if widget_is_mixed(w):
                continue
            if isinstance(w, QLineEdit):
                kwargs[attr] = w.text()
            else:
                kwargs[attr] = w.value()

        for eid in ids:
            self._model.update_fuel_source(eid, **kwargs)
            self.fuelSourceChanged.emit(eid)

    def _on_table_changed(self, row: int, col: int):
        if self._updating or not self._current_id:
            return
        source = self._model.state.fuel_sources.get(self._current_id)
        if not source:
            return
        item = self._node_table.item(row, col)
        if not item:
            return
        try:
            val = float(item.text())
        except ValueError:
            return
        arrays = [
            source.max_availability,
            source.import_cost,
            source.storage_capacity,
            source.initial_storage_level,
        ]
        if row < len(arrays[col]):
            arrays[col][row] = val
        self._model.fuelSourceUpdated.emit(self._current_id)
        self.fuelSourceChanged.emit(self._current_id)
