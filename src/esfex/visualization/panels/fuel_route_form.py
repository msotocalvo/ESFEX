"""Fuel transport route properties form."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import (
    RENEWABLE_FUELS,
    FuelRouteParams,
    GuiModel,
)
from esfex.visualization.i18n import tr
from esfex.visualization.panels.visual_style_widget import VisualStyleWidget
from esfex.visualization.panels.word_wrap_header import WordWrapHeaderView

# Column indices for the fuels table
_COL_FUEL = 0
_COL_CAPACITY = 1
_COL_COST = 2
_COL_LOSSES = 3


class FuelRouteForm(QWidget):
    """Property editor for a fuel transport route."""

    fuelRouteChanged = Signal(str)
    fuelRouteDeleteRequested = Signal(str)
    editTraceToggled = Signal(str, bool)  # route_id, enabled

    def __init__(self, model: GuiModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._current_id: str | None = None
        self._multi_ids: list[str] | None = None
        self._updating = False
        self._editing_trace = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # --- Header: system + endpoints ---
        self._header_label = QLabel("")
        self._header_label.setObjectName("headerLabel")
        outer.addWidget(self._header_label)

        # --- Fuels (table format like FuelEntry / FuelStorage) ---
        fuel_grp = QGroupBox(tr("fuel_route_form.group_fuels"))
        fuel_layout = QVBoxLayout(fuel_grp)
        fuel_layout.setContentsMargins(6, 6, 6, 6)
        fuel_layout.setSpacing(4)

        # Fuel selector (combo + add/remove) — above the table
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

        # Fuels table: Fuel | Capacity | Transport Cost | Losses
        self._fuel_table = QTableWidget()
        self._fuel_table.setHorizontalHeader(WordWrapHeaderView(self._fuel_table))
        self._fuel_table.setColumnCount(4)
        self._fuel_table.setHorizontalHeaderLabels([
            tr("generator_form.fuel"),
            tr("fuel_route_form.capacity"),
            tr("fuel_route_form.transport_cost"),
            tr("fuel_route_form.losses"),
        ])
        self._fuel_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._fuel_table.setMinimumHeight(80)
        self._fuel_table.setMaximumHeight(260)
        self._fuel_table.cellChanged.connect(self._on_table_changed)
        fuel_layout.addWidget(self._fuel_table)
        outer.addWidget(fuel_grp)

        # --- Route Properties (length) ---
        prop_grp = QGroupBox(tr("fuel_route_form.group_properties"))
        prop_layout = QFormLayout(prop_grp)
        prop_layout.setContentsMargins(6, 6, 6, 6)
        prop_layout.setSpacing(4)

        self._length_km = QDoubleSpinBox()
        self._length_km.setRange(0, 100000)
        self._length_km.setDecimals(2)
        self._length_km.setSpecialValueText("Auto")
        self._length_km.setReadOnly(True)
        self._length_km.setObjectName("readOnlyField")
        prop_layout.addRow(tr("fuel_route_form.length"), self._length_km)
        outer.addWidget(prop_grp)

        # -- Appearance --
        self._style_widget = VisualStyleWidget(
            show_color=True, show_width=True, show_size=False,
        )
        self._style_widget.styleChanged.connect(self._on_style_changed)
        outer.addWidget(self._style_widget)

        # -- Edit / Delete buttons --
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(4, 2, 4, 2)

        self._edit_trace_btn = QPushButton(tr("fuel_route_form.edit_trace"))
        self._edit_trace_btn.setCheckable(True)
        self._edit_trace_btn.setObjectName("editTraceButton")
        self._edit_trace_btn.toggled.connect(self._on_edit_trace_toggled)
        btn_row.addWidget(self._edit_trace_btn)

        self._delete_btn = QPushButton(tr("fuel_route_form.delete_route"))
        self._delete_btn.setObjectName("deleteButton")
        self._delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self._delete_btn)

        outer.addLayout(btn_row)
        outer.addStretch()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_elements(self, element_ids: list[str]):
        """Load multiple fuel routes for batch editing."""
        instances = self._get_routes(element_ids)
        if not instances:
            return
        self._multi_ids = element_ids
        self._current_id = element_ids[0]
        self._header_label.setText(tr("fuel_route_form.n_selected", n=len(element_ids)))
        self._updating = True

        # Clear fuel table (not meaningful for multi-edit)
        self._fuel_table.blockSignals(True)
        self._fuel_table.setRowCount(0)
        self._fuel_table.blockSignals(False)
        self._fuel_combo.clear()

        # Disable trace editing
        self._edit_trace_btn.setChecked(False)
        self._edit_trace_btn.setEnabled(False)
        self._editing_trace = False

        self._length_km.setValue(0)

        self._updating = False

    def load_element(self, element_id: str):
        self._multi_ids = None
        self._edit_trace_btn.setEnabled(True)
        self._current_id = element_id
        route = self._get_current_route()
        if not route:
            return

        self._updating = True

        # Header: system + node endpoints
        from_name = ""
        to_name = ""
        for n in self._model.state.nodes:
            if n.index == route.from_node:
                from_name = n.name
            if n.index == route.to_node:
                to_name = n.name
        self._header_label.setText(
            f"{self._model.state.name}  |  {from_name} \u2192 {to_name}"
        )

        # Populate fuel combo and table
        self._populate_fuel_combo()
        self._rebuild_fuel_table()

        # Length
        self._length_km.setValue(route.length_km or 0)

        from esfex.visualization.data.default_colors import FUEL_ROUTE
        self._style_widget.set_default_color(FUEL_ROUTE)
        self._style_widget.load_style(route.style)

        # Reset edit state
        self._edit_trace_btn.setChecked(False)
        self._editing_trace = False

        self._updating = False

    # ------------------------------------------------------------------
    # Fuel table
    # ------------------------------------------------------------------

    def _rebuild_fuel_table(self):
        """Populate the fuels table from the current route's data."""
        route = self._get_current_route()
        self._fuel_table.blockSignals(True)
        self._fuel_table.setRowCount(0)
        if route:
            for row, fuel_name in enumerate(route.fuels):
                params = route.fuel_params.get(fuel_name, FuelRouteParams())
                self._fuel_table.insertRow(row)
                # Fuel name (read-only)
                name_item = QTableWidgetItem(fuel_name)
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._fuel_table.setItem(row, _COL_FUEL, name_item)
                # Capacity
                self._fuel_table.setItem(
                    row, _COL_CAPACITY,
                    QTableWidgetItem(f"{params.capacity:.2f}"),
                )
                # Transport cost
                self._fuel_table.setItem(
                    row, _COL_COST,
                    QTableWidgetItem(f"{params.transport_cost:.4f}"),
                )
                # Losses
                self._fuel_table.setItem(
                    row, _COL_LOSSES,
                    QTableWidgetItem(f"{params.losses_fraction:.4f}"),
                )
        self._fuel_table.blockSignals(False)

    def _populate_fuel_combo(self):
        """Populate combo with fuels not yet added (excluding renewables)."""
        self._fuel_combo.clear()
        route = self._get_current_route()
        current_fuels = set(route.fuels) if route else set()
        for fid in self._model.state.fuels:
            if fid not in current_fuels and fid not in RENEWABLE_FUELS:
                self._fuel_combo.addItem(fid)

    def _on_add_fuel(self):
        fuel = self._fuel_combo.currentText()
        if not fuel:
            return
        route = self._get_current_route()
        if not route or fuel in route.fuels:
            return
        route.fuels.append(fuel)
        route.fuel_params[fuel] = FuelRouteParams()
        self._updating = True
        self._rebuild_fuel_table()
        self._populate_fuel_combo()
        self._updating = False
        self._emit_changed()

    def _on_remove_fuel(self):
        row = self._fuel_table.currentRow()
        if row < 0:
            return
        item = self._fuel_table.item(row, _COL_FUEL)
        if not item:
            return
        fuel = item.text()
        route = self._get_current_route()
        if route and fuel in route.fuels:
            route.fuels.remove(fuel)
        if route and fuel in route.fuel_params:
            del route.fuel_params[fuel]
        self._updating = True
        self._rebuild_fuel_table()
        self._populate_fuel_combo()
        self._updating = False
        self._emit_changed()

    def _on_table_changed(self, row: int, col: int):
        """Save table edits back to the model."""
        if self._updating or self._current_id is None:
            return
        route = self._get_current_route()
        if not route:
            return
        fuel_item = self._fuel_table.item(row, _COL_FUEL)
        if not fuel_item:
            return
        fuel = fuel_item.text()
        params = route.fuel_params.get(fuel)
        if params is None:
            params = FuelRouteParams()
            route.fuel_params[fuel] = params
        try:
            val = float(self._fuel_table.item(row, col).text())
        except (ValueError, AttributeError):
            return
        if col == _COL_CAPACITY:
            params.capacity = val
        elif col == _COL_COST:
            params.transport_cost = val
        elif col == _COL_LOSSES:
            params.losses_fraction = val
        self._emit_changed()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_routes(self, ids: list[str]):
        """Get route instances by route_id."""
        id_set = set(ids)
        return [rt for rt in self._model.state.fuel_transport_routes if rt.route_id in id_set]

    def _get_current_route(self):
        if not self._current_id:
            return None
        for rt in self._model.state.fuel_transport_routes:
            if rt.route_id == self._current_id:
                return rt
        return None

    def set_length_km(self, km: float):
        """Update the displayed length (called by main_window on trace changes)."""
        self._updating = True
        self._length_km.setValue(km)
        self._updating = False

    def stop_editing(self):
        """Disable trace editing (called on deselection)."""
        if self._editing_trace and self._current_id:
            self._edit_trace_btn.setChecked(False)

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _on_edit_trace_toggled(self, checked: bool):
        if self._current_id is None:
            return
        self._editing_trace = checked
        self.editTraceToggled.emit(self._current_id, checked)

    def _on_delete(self):
        if self._current_id is None:
            return
        from esfex.visualization.panels._dialogs import confirm_delete
        if confirm_delete(
            self,
            tr("fuel_route_form.confirm_delete_title"),
            tr("fuel_route_form.confirm_delete_msg"),
        ):
            if self._editing_trace:
                self._edit_trace_btn.setChecked(False)
            self.fuelRouteDeleteRequested.emit(self._current_id)

    def _on_style_changed(self):
        if self._updating or not self._current_id:
            return
        route = self._get_current_route()
        if route:
            route.style = self._style_widget.get_style()
        self._emit_changed()

    def _emit_changed(self):
        """Notify that route data changed."""
        if self._current_id:
            self._model.fuelRouteUpdated.emit(self._current_id)
            self.fuelRouteChanged.emit(self._current_id)
