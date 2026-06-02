"""Property editor for a fuel storage facility."""

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
    FuelStorageParams,
    GuiModel,
)
from esfex.visualization.i18n import tr
from esfex.visualization.panels.visual_style_widget import VisualStyleWidget
from esfex.visualization.panels.word_wrap_header import WordWrapHeaderView

# Column indices for the fuels table
_COL_FUEL = 0
_COL_CAPACITY = 1
_COL_INITIAL = 2
_COL_MIN = 3


class FuelStorageForm(QWidget):
    """Property editor for a :class:`GuiFuelStorage`."""

    fuelStorageChanged = Signal(str)  # storage_id

    def __init__(self, model: GuiModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._current_id: str | None = None
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

        self._node_combo = QComboBox()
        self._node_combo.currentIndexChanged.connect(self._on_changed)
        id_layout.addRow(tr("generator_form.node"), self._node_combo)
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

        # Fuels table: Fuel | Capacity | Initial Level | Min Level
        self._fuel_table = QTableWidget()
        self._fuel_table.setHorizontalHeader(WordWrapHeaderView(self._fuel_table))
        self._fuel_table.setColumnCount(4)
        self._fuel_table.setHorizontalHeaderLabels([
            tr("generator_form.fuel"),
            tr("fuel_storage_form.capacity"),
            tr("fuel_storage_form.initial_level"),
            tr("fuel_storage_form.min_level"),
        ])
        self._fuel_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._fuel_table.setMinimumHeight(80)
        self._fuel_table.setMaximumHeight(260)
        self._fuel_table.cellChanged.connect(self._on_table_changed)
        fuel_layout.addWidget(self._fuel_table)
        outer.addWidget(fuel_grp)

        # --- Appearance ---
        self._style_widget = VisualStyleWidget(show_color=True, show_size=True)
        self._style_widget.styleChanged.connect(self._on_changed)
        outer.addWidget(self._style_widget)

        outer.addStretch()

    def load_element(self, element_id: str):
        inst = self._model.state.fuel_storages.get(element_id)
        if not inst:
            return
        self._current_id = element_id
        self._system_label.setText(
            tr("fuel_storage_form.system_label", name=self._model.state.name)
        )
        self._updating = True

        self._name.setText(inst.name)

        # Node combo
        self._node_combo.blockSignals(True)
        self._node_combo.clear()
        for nd in self._model.state.nodes:
            self._node_combo.addItem(
                tr("node_form.node_combo_fmt", idx=nd.index, name=nd.name),
                nd.index,
            )
        idx = self._node_combo.findData(inst.node)
        if idx >= 0:
            self._node_combo.setCurrentIndex(idx)
        self._node_combo.blockSignals(False)

        self._populate_fuel_combo()
        self._rebuild_fuel_table()

        from esfex.visualization.data.default_colors import FUEL_STORAGE

        self._style_widget.set_default_color(FUEL_STORAGE)
        self._style_widget.load_style(inst.style)
        self._updating = False

    def _rebuild_fuel_table(self):
        """Populate the fuels table from the current storage's data."""
        inst = self._get_current()
        self._fuel_table.blockSignals(True)
        self._fuel_table.setRowCount(0)
        if inst:
            for row, fuel_name in enumerate(inst.fuels):
                params = inst.fuel_params.get(fuel_name, FuelStorageParams())
                self._fuel_table.insertRow(row)
                # Fuel name (read-only)
                name_item = QTableWidgetItem(fuel_name)
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._fuel_table.setItem(row, _COL_FUEL, name_item)
                # Capacity
                self._fuel_table.setItem(
                    row, _COL_CAPACITY,
                    QTableWidgetItem(f"{params.capacity:.1f}"),
                )
                # Initial level
                self._fuel_table.setItem(
                    row, _COL_INITIAL,
                    QTableWidgetItem(f"{params.initial_level:.3f}"),
                )
                # Min level
                self._fuel_table.setItem(
                    row, _COL_MIN,
                    QTableWidgetItem(f"{params.min_level:.3f}"),
                )
        self._fuel_table.blockSignals(False)

    def _populate_fuel_combo(self):
        """Populate combo with fuels not yet added (excluding renewables)."""
        self._fuel_combo.clear()
        inst = self._get_current()
        current_fuels = set(inst.fuels) if inst else set()
        for fid in self._model.state.fuels:
            if fid not in current_fuels and fid not in RENEWABLE_FUELS:
                self._fuel_combo.addItem(fid)

    def _get_current(self):
        if self._current_id is None:
            return None
        return self._model.state.fuel_storages.get(self._current_id)

    def _on_add_fuel(self):
        fuel = self._fuel_combo.currentText()
        if not fuel:
            return
        inst = self._get_current()
        if not inst or fuel in inst.fuels:
            return
        inst.fuels.append(fuel)
        inst.fuel_params[fuel] = FuelStorageParams()
        self._updating = True
        self._rebuild_fuel_table()
        self._populate_fuel_combo()
        self._updating = False
        if self._current_id is not None:
            self._model.fuelStorageUpdated.emit(self._current_id)
            self.fuelStorageChanged.emit(self._current_id)

    def _on_remove_fuel(self):
        row = self._fuel_table.currentRow()
        if row < 0:
            return
        item = self._fuel_table.item(row, _COL_FUEL)
        if not item:
            return
        fuel = item.text()
        inst = self._get_current()
        if inst and fuel in inst.fuels:
            inst.fuels.remove(fuel)
        if inst and fuel in inst.fuel_params:
            del inst.fuel_params[fuel]
        self._updating = True
        self._rebuild_fuel_table()
        self._populate_fuel_combo()
        self._updating = False
        if self._current_id is not None:
            self._model.fuelStorageUpdated.emit(self._current_id)
            self.fuelStorageChanged.emit(self._current_id)

    def _on_table_changed(self, row: int, col: int):
        """Save table edits back to the model."""
        if self._updating or self._current_id is None:
            return
        inst = self._get_current()
        if not inst:
            return
        fuel_item = self._fuel_table.item(row, _COL_FUEL)
        if not fuel_item:
            return
        fuel = fuel_item.text()
        params = inst.fuel_params.get(fuel)
        if params is None:
            params = FuelStorageParams()
            inst.fuel_params[fuel] = params
        try:
            val = float(self._fuel_table.item(row, col).text())
        except (ValueError, AttributeError):
            return
        if col == _COL_CAPACITY:
            params.capacity = val
        elif col == _COL_INITIAL:
            params.initial_level = val
        elif col == _COL_MIN:
            params.min_level = val
        self._model.fuelStorageUpdated.emit(self._current_id)
        self.fuelStorageChanged.emit(self._current_id)

    def _on_changed(self):
        if self._updating or self._current_id is None:
            return
        self._model.checkpoint()
        inst = self._model.state.fuel_storages.get(self._current_id)
        if not inst:
            return
        inst.name = self._name.text()
        nd = self._node_combo.currentData()
        if nd is not None:
            inst.node = nd
        inst.style = self._style_widget.get_style()
        self._model.fuelStorageUpdated.emit(self._current_id)
        self.fuelStorageChanged.emit(self._current_id)
