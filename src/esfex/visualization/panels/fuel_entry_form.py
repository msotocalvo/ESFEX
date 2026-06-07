"""Fuel entry point properties form."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import (
    RENEWABLE_FUELS,
    FuelEntryParams,
    GuiModel,
)
from esfex.visualization.i18n import tr
from esfex.visualization.panels.visual_style_widget import VisualStyleWidget
from esfex.visualization.panels.word_wrap_header import WordWrapHeaderView

# Column indices for the fuels table
_COL_FUEL = 0
_COL_MAX_IMPORT = 1
_COL_IMPORT_COST = 2
_COL_TRANSIT = 3
_COL_DISRUPT_START = 4
_COL_DISRUPT_END = 5
_COL_DISRUPT_AVAIL = 6


class FuelEntryForm(QWidget):
    """Property editor for a fuel entry point."""

    fuelEntryChanged = Signal(int)

    def __init__(self, model: GuiModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._current_idx: int | None = None
        self._updating = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # --- Identity ---
        id_grp = QGroupBox(tr("fuel_entry_form.group_identity"))
        id_layout = QFormLayout(id_grp)
        id_layout.setContentsMargins(6, 6, 6, 6)
        id_layout.setSpacing(4)

        self._system_label = QLabel("")
        id_layout.addRow(tr("generator_form.system_label", name=""), self._system_label)

        self._name = QLineEdit()
        self._name.editingFinished.connect(self._on_changed)
        id_layout.addRow(tr("generator_form.name"), self._name)

        self._node = QComboBox()
        self._node.currentIndexChanged.connect(self._on_changed)
        id_layout.addRow(tr("generator_form.node"), self._node)

        self._lat = QDoubleSpinBox()
        self._lat.setRange(-90, 90)
        self._lat.setDecimals(6)
        self._lat.editingFinished.connect(self._on_changed)
        id_layout.addRow(tr("fuel_entry_form.latitude"), self._lat)

        self._lng = QDoubleSpinBox()
        self._lng.setRange(-180, 180)
        self._lng.setDecimals(6)
        self._lng.editingFinished.connect(self._on_changed)
        id_layout.addRow(tr("fuel_entry_form.longitude"), self._lng)
        outer.addWidget(id_grp)

        # --- Fuels ---
        fuel_grp = QGroupBox(tr("fuel_entry_form.group_fuels"))
        fuel_layout = QVBoxLayout(fuel_grp)
        fuel_layout.setContentsMargins(6, 6, 6, 6)
        fuel_layout.setSpacing(4)

        # Fuel selector (combo + add/remove)
        fuel_btn_row = QHBoxLayout()
        self._fuel_combo = QComboBox()
        fuel_btn_row.addWidget(self._fuel_combo, 1)
        self._add_fuel_btn = QPushButton(tr("common.add"))
        self._add_fuel_btn.clicked.connect(self._on_add_fuel)
        fuel_btn_row.addWidget(self._add_fuel_btn)
        self._remove_fuel_btn = QPushButton(tr("common.remove"))
        self._remove_fuel_btn.clicked.connect(self._on_remove_fuel)
        fuel_btn_row.addWidget(self._remove_fuel_btn)
        fuel_layout.addLayout(fuel_btn_row)

        # Fuels table: Fuel | Max Import | Import Cost
        self._fuel_table = QTableWidget()
        self._fuel_table.setHorizontalHeader(WordWrapHeaderView(self._fuel_table))
        self._fuel_table.setColumnCount(7)
        self._fuel_table.setHorizontalHeaderLabels([
            tr("generator_form.fuel"),
            tr("fuel_entry_form.max_import_rate"),
            tr("fuel_entry_form.import_cost"),
            tr("fuel_entry_form.transport_transit_days_per_100km"),
            tr("fuel_entry_form.disruption_start_hour"),
            tr("fuel_entry_form.disruption_end_hour"),
            tr("fuel_entry_form.disruption_availability"),
        ])
        self._fuel_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._fuel_table.setMinimumHeight(80)
        self._fuel_table.setMaximumHeight(260)
        self._fuel_table.cellChanged.connect(self._on_table_changed)
        fuel_layout.addWidget(self._fuel_table)
        outer.addWidget(fuel_grp)

        # -- Appearance --
        self._style_widget = VisualStyleWidget(
            show_color=True, show_size=True,
        )
        self._style_widget.styleChanged.connect(self._on_changed)
        outer.addWidget(self._style_widget)

        outer.addStretch()

    def load_element(self, element_id: str):
        # Strip system prefix if present (e.g. "Cuba/5" → "5")
        raw_id = element_id.rsplit("/", 1)[-1] if "/" in element_id else element_id
        try:
            idx = int(raw_id)
        except ValueError:
            return
        if idx >= len(self._model.state.fuel_entry_points):
            return
        self._current_idx = idx
        self._system_label.setText(tr("fuel_entry_form.system_label", name=self._model.state.name))
        fe = self._model.state.fuel_entry_points[idx]

        self._updating = True
        self._name.setText(fe.name)

        self._populate_fuel_combo()
        self._rebuild_fuel_table()

        self._node.clear()
        for n in self._model.state.nodes:
            self._node.addItem(n.name, n.index)
        node_idx = self._node.findData(fe.node)
        if node_idx >= 0:
            self._node.setCurrentIndex(node_idx)

        self._lat.setValue(fe.coordinate.lat)
        self._lng.setValue(fe.coordinate.lng)

        from esfex.visualization.data.default_colors import FUEL_ENTRY
        self._style_widget.set_default_color(FUEL_ENTRY)
        self._style_widget.load_style(fe.style)
        self._updating = False

    def _rebuild_fuel_table(self):
        """Populate the fuels table from the current fuel entry's data."""
        fe = self._get_current_fe()
        self._fuel_table.blockSignals(True)
        self._fuel_table.setRowCount(0)
        if fe:
            for row, fuel_name in enumerate(fe.fuels):
                params = fe.fuel_params.get(fuel_name, FuelEntryParams())
                self._fuel_table.insertRow(row)
                # Fuel name (read-only)
                name_item = QTableWidgetItem(fuel_name)
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._fuel_table.setItem(row, _COL_FUEL, name_item)
                # Max import rate
                self._fuel_table.setItem(
                    row, _COL_MAX_IMPORT,
                    QTableWidgetItem(f"{params.max_import_rate:.2f}"),
                )
                # Import cost
                self._fuel_table.setItem(
                    row, _COL_IMPORT_COST,
                    QTableWidgetItem(f"{params.import_cost:.2f}"),
                )
                # Supply-stress params (source->tank transit, disruption window)
                self._fuel_table.setItem(
                    row, _COL_TRANSIT,
                    QTableWidgetItem(f"{params.transport_transit_days_per_100km:.2f}"),
                )
                self._fuel_table.setItem(
                    row, _COL_DISRUPT_START,
                    QTableWidgetItem(f"{int(params.disruption_start_hour)}"),
                )
                self._fuel_table.setItem(
                    row, _COL_DISRUPT_END,
                    QTableWidgetItem(f"{int(params.disruption_end_hour)}"),
                )
                self._fuel_table.setItem(
                    row, _COL_DISRUPT_AVAIL,
                    QTableWidgetItem(f"{params.disruption_availability:.3f}"),
                )
        self._fuel_table.blockSignals(False)

    def _populate_fuel_combo(self):
        """Populate combo with fuels not yet added (excluding renewables)."""
        self._fuel_combo.clear()
        fe = self._get_current_fe()
        current_fuels = set(fe.fuels) if fe else set()
        for fid in self._model.state.fuels:
            if fid not in current_fuels and fid not in RENEWABLE_FUELS:
                self._fuel_combo.addItem(fid)

    def _get_current_fe(self):
        if self._current_idx is None:
            return None
        if self._current_idx < len(self._model.state.fuel_entry_points):
            return self._model.state.fuel_entry_points[self._current_idx]
        return None

    def _on_add_fuel(self):
        fuel = self._fuel_combo.currentText()
        if not fuel:
            return
        fe = self._get_current_fe()
        if not fe or fuel in fe.fuels:
            return
        fe.fuels.append(fuel)
        fe.fuel_params[fuel] = FuelEntryParams()
        self._updating = True
        self._rebuild_fuel_table()
        self._populate_fuel_combo()
        self._updating = False
        if self._current_idx is not None:
            self.fuelEntryChanged.emit(self._current_idx)

    def _on_remove_fuel(self):
        row = self._fuel_table.currentRow()
        if row < 0:
            return
        item = self._fuel_table.item(row, _COL_FUEL)
        if not item:
            return
        fuel = item.text()
        fe = self._get_current_fe()
        if fe and fuel in fe.fuels:
            fe.fuels.remove(fuel)
        if fe and fuel in fe.fuel_params:
            del fe.fuel_params[fuel]
        self._updating = True
        self._rebuild_fuel_table()
        self._populate_fuel_combo()
        self._updating = False
        if self._current_idx is not None:
            self.fuelEntryChanged.emit(self._current_idx)

    def _on_table_changed(self, row: int, col: int):
        """Save table edits back to the model."""
        if self._updating or self._current_idx is None:
            return
        fe = self._get_current_fe()
        if not fe:
            return
        fuel_item = self._fuel_table.item(row, _COL_FUEL)
        if not fuel_item:
            return
        fuel = fuel_item.text()
        params = fe.fuel_params.get(fuel)
        if params is None:
            params = FuelEntryParams()
            fe.fuel_params[fuel] = params
        try:
            val = float(self._fuel_table.item(row, col).text())
        except (ValueError, AttributeError):
            return
        if col == _COL_MAX_IMPORT:
            params.max_import_rate = val
        elif col == _COL_IMPORT_COST:
            params.import_cost = val
        elif col == _COL_TRANSIT:
            params.transport_transit_days_per_100km = max(0.0, val)
        elif col == _COL_DISRUPT_START:
            params.disruption_start_hour = max(0, int(val))
        elif col == _COL_DISRUPT_END:
            params.disruption_end_hour = max(0, int(val))
        elif col == _COL_DISRUPT_AVAIL:
            params.disruption_availability = min(1.0, max(0.0, val))
        self.fuelEntryChanged.emit(self._current_idx)

    def _on_changed(self):
        if self._updating or self._current_idx is None:
            return
        self._model.checkpoint()
        fe = self._model.state.fuel_entry_points[self._current_idx]
        fe.name = self._name.text()
        fe.node = self._node.currentData() or 0
        fe.coordinate.lat = self._lat.value()
        fe.coordinate.lng = self._lng.value()
        fe.style = self._style_widget.get_style()
        self.fuelEntryChanged.emit(self._current_idx)
