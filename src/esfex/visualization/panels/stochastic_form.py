"""Stochastic scenarios configuration form."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import GuiModel, GuiStochasticScenario
from esfex.visualization.i18n import tr
from esfex.visualization.panels.word_wrap_header import WordWrapHeaderView

# All cost-affecting elements that can be used as stochastic multipliers.
# Grouped logically: (key, display_label)
_COST_MULTIPLIER_CATALOG: list[tuple[str, str, str]] = [
    # --- Generator investment ---
    ("gen_invest_cost", "stochastic_form.mult_gen_invest_cost", "stochastic_form.group_investment"),
    ("gen_decommissioning_cost", "stochastic_form.mult_gen_decommissioning_cost", "stochastic_form.group_investment"),
    # --- Generator operational ---
    ("gen_fuel_cost", "stochastic_form.mult_gen_fuel_cost", "stochastic_form.group_operational"),
    ("gen_fixed_cost", "stochastic_form.mult_gen_fixed_cost", "stochastic_form.group_operational"),
    ("gen_maintenance_cost", "stochastic_form.mult_gen_maintenance_cost", "stochastic_form.group_operational"),
    ("gen_start_up_cost", "stochastic_form.mult_gen_start_up_cost", "stochastic_form.group_operational"),
    # --- Battery / Storage ---
    ("bat_invest_cost_power", "stochastic_form.mult_bat_invest_cost_power", "stochastic_form.group_investment"),
    ("bat_invest_cost_capacity", "stochastic_form.mult_bat_invest_cost_capacity", "stochastic_form.group_investment"),
    ("bat_maintenance_cost", "stochastic_form.mult_bat_maintenance_cost", "stochastic_form.group_operational"),
    ("bat_decommissioning_cost", "stochastic_form.mult_bat_decommissioning_cost", "stochastic_form.group_investment"),
    # --- Transmission ---
    ("transmission_invest_cost", "stochastic_form.mult_transmission_invest_cost", "stochastic_form.group_investment"),
    ("transmission_cost_per_mw_km", "stochastic_form.mult_transmission_cost_per_mw_km", "stochastic_form.group_investment"),
    # --- Electrolyzer ---
    ("electrolyzer_invest_cost", "stochastic_form.mult_electrolyzer_invest_cost", "stochastic_form.group_investment"),
    ("electrolyzer_fixed_cost", "stochastic_form.mult_electrolyzer_fixed_cost", "stochastic_form.group_operational"),
    ("electrolyzer_variable_cost", "stochastic_form.mult_electrolyzer_variable_cost", "stochastic_form.group_operational"),
    ("electrolyzer_water_cost", "stochastic_form.mult_electrolyzer_water_cost", "stochastic_form.group_operational"),
    # --- Converters ---
    ("converter_invest_cost", "stochastic_form.mult_converter_invest_cost", "stochastic_form.group_investment"),
    ("converter_variable_cost", "stochastic_form.mult_converter_variable_cost", "stochastic_form.group_operational"),
    # --- Primary energy / Fuel supply ---
    ("fuel_import_cost", "stochastic_form.mult_fuel_import_cost", "stochastic_form.group_fuel"),
    ("fuel_price", "stochastic_form.mult_fuel_price", "stochastic_form.group_fuel"),
    ("fuel_transport_cost", "stochastic_form.mult_fuel_transport_cost", "stochastic_form.group_fuel"),
    ("fuel_storage_invest_cost", "stochastic_form.mult_fuel_storage_invest_cost", "stochastic_form.group_fuel"),
    ("fuel_price_growth_rate", "stochastic_form.mult_fuel_price_growth_rate", "stochastic_form.group_fuel"),
    # --- Penalties ---
    ("penalty_loss_of_load", "stochastic_form.mult_penalty_loss_of_load", "stochastic_form.group_penalty"),
    ("penalty_co2_cost", "stochastic_form.mult_penalty_co2_cost", "stochastic_form.group_penalty"),
    ("penalty_co2_budget_violation", "stochastic_form.mult_penalty_co2_budget_violation", "stochastic_form.group_penalty"),
    ("penalty_loss_of_reserve", "stochastic_form.mult_penalty_loss_of_reserve", "stochastic_form.group_penalty"),
    ("penalty_curtailment", "stochastic_form.mult_penalty_curtailment", "stochastic_form.group_penalty"),
    ("penalty_loss_of_fuel_supply", "stochastic_form.mult_penalty_loss_of_fuel_supply", "stochastic_form.group_penalty"),
    ("penalty_fre_penetration", "stochastic_form.mult_penalty_fre_penetration", "stochastic_form.group_penalty"),
    # --- Demand ---
    ("demand_growth", "stochastic_form.mult_demand_growth", "stochastic_form.group_demand"),
    # --- Financial ---
    ("discount_rate", "stochastic_form.mult_discount_rate", "stochastic_form.group_financial"),
]


class StochasticForm(QWidget):
    """Property editor for stochastic scenarios."""

    scenariosChanged = Signal()

    def __init__(self, model: GuiModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._updating = False
        self._mult_keys: list[str] = []  # multiplier keys in display order

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # --- Scenario list ---
        grp = QGroupBox(tr("stochastic_form.group_scenarios"))
        sec_layout = QVBoxLayout(grp)

        btn_row = QHBoxLayout()
        self._new_name = QLineEdit()
        self._new_name.setPlaceholderText(tr("stochastic_form.name_placeholder"))
        btn_row.addWidget(self._new_name)
        add_btn = QPushButton(tr("stochastic_form.add_btn"))
        add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(add_btn)
        rm_btn = QPushButton(tr("stochastic_form.remove_btn"))
        rm_btn.clicked.connect(self._on_remove)
        btn_row.addWidget(rm_btn)
        sec_layout.addLayout(btn_row)

        # Main table: Name, Probability, Description
        self._main_table = QTableWidget()
        self._main_table.setHorizontalHeader(WordWrapHeaderView(self._main_table))
        self._main_table.setColumnCount(3)
        self._main_table.setHorizontalHeaderLabels([
            tr("stochastic_form.col_name"),
            tr("stochastic_form.col_probability"),
            tr("stochastic_form.col_description"),
        ])
        self._main_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._main_table.cellChanged.connect(self._on_main_changed)
        sec_layout.addWidget(self._main_table)

        outer.addWidget(grp)

        # --- Multipliers table (TRANSPOSED) ---
        grp_mult = QGroupBox(tr("stochastic_form.group_multipliers"))
        mult_layout = QVBoxLayout(grp_mult)

        # Buttons for adding/removing multiplier rows
        mult_btn_row = QHBoxLayout()
        add_mult_btn = QPushButton(tr("stochastic_form.add_mult_btn"))
        add_mult_btn.clicked.connect(self._on_add_multiplier)
        mult_btn_row.addWidget(add_mult_btn)
        rm_mult_btn = QPushButton(tr("stochastic_form.remove_mult_btn"))
        rm_mult_btn.clicked.connect(self._on_remove_multiplier)
        mult_btn_row.addWidget(rm_mult_btn)
        mult_btn_row.addStretch()
        mult_layout.addLayout(mult_btn_row)

        # Transposed table: rows = multiplier keys, columns = scenario names
        self._mult_table = QTableWidget()
        self._mult_table.setHorizontalHeader(WordWrapHeaderView(self._mult_table))
        self._mult_table.cellChanged.connect(self._on_mult_changed)
        mult_layout.addWidget(self._mult_table)

        outer.addWidget(grp_mult)
        outer.addStretch()

    def load_element(self, element_id: str = ""):
        """Load stochastic scenarios. element_id is ignored."""
        self._updating = True
        scenarios = self._model.stochastic_scenarios

        # --- Load main table ---
        self._main_table.setRowCount(len(scenarios))

        for i, sc in enumerate(scenarios):
            self._main_table.setItem(i, 0, QTableWidgetItem(sc.name))
            self._main_table.setItem(
                i, 1, QTableWidgetItem(f"{sc.probability}")
            )
            self._main_table.setItem(i, 2, QTableWidgetItem(sc.description))

        # --- Load multipliers table (TRANSPOSED) ---
        # Collect union of all multiplier keys across all scenarios
        all_keys = set()
        for sc in scenarios:
            all_keys.update(sc.multipliers.keys())

        # Sort keys by catalog order, then alphabetically for unknown keys
        catalog_order = {k: i for i, (k, _, _) in enumerate(_COST_MULTIPLIER_CATALOG)}
        sorted_keys = sorted(all_keys, key=lambda k: (catalog_order.get(k, 9999), k))
        self._mult_keys = sorted_keys

        # Set table dimensions: rows = multiplier keys, columns = scenario names
        self._mult_table.setRowCount(len(sorted_keys))
        self._mult_table.setColumnCount(len(scenarios))

        # Set horizontal headers (scenario names)
        scenario_names = [sc.name for sc in scenarios]
        self._mult_table.setHorizontalHeaderLabels(scenario_names)

        # Set vertical headers (display labels from catalog, fallback to key)
        key_to_label = {k: tr_key for k, tr_key, _ in _COST_MULTIPLIER_CATALOG}
        display_labels = [tr(key_to_label[k]) if k in key_to_label else k for k in sorted_keys]
        self._mult_table.setVerticalHeaderLabels(display_labels)

        # Fill in the cell values
        for row_idx, key in enumerate(sorted_keys):
            for col_idx, sc in enumerate(scenarios):
                value = sc.multipliers.get(key, 1.0)
                self._mult_table.setItem(
                    row_idx, col_idx, QTableWidgetItem(f"{value}")
                )

        # Resize columns to fit content
        self._mult_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

        self._updating = False

    def _on_add(self):
        """Add a new scenario."""
        name = self._new_name.text().strip()
        if not name:
            return
        # Check for duplicate
        for sc in self._model.stochastic_scenarios:
            if sc.name == name:
                QMessageBox.warning(
                    self,
                    tr("messages.duplicate_name_title"),
                    tr("messages.duplicate_name", name=name),
                )
                return

        # Create new scenario with default multipliers matching existing keys
        new_scenario = GuiStochasticScenario(name=name)

        # If there are existing scenarios, copy their multiplier keys
        if self._model.stochastic_scenarios:
            all_keys = set()
            for sc in self._model.stochastic_scenarios:
                all_keys.update(sc.multipliers.keys())
            # Initialize with default value 1.0
            for key in all_keys:
                new_scenario.multipliers[key] = 1.0

        self._model.stochastic_scenarios.append(new_scenario)
        self._new_name.clear()
        self.load_element()
        self.scenariosChanged.emit()

    def _on_remove(self):
        """Remove the selected scenario."""
        row = self._main_table.currentRow()
        if row < 0 or row >= len(self._model.stochastic_scenarios):
            return
        self._model.stochastic_scenarios.pop(row)
        self.load_element()
        self.scenariosChanged.emit()

    def _on_add_multiplier(self):
        """Add multiplier(s) from the catalog to all scenarios."""
        # Collect keys already present
        existing_keys: set[str] = set()
        for sc in self._model.stochastic_scenarios:
            existing_keys.update(sc.multipliers.keys())

        # Build list of available (not yet added) items
        available = [
            (key, label, group)
            for key, label, group in _COST_MULTIPLIER_CATALOG
            if key not in existing_keys
        ]

        if not available:
            QMessageBox.information(
                self,
                tr("stochastic_form.invalid_mult_title"),
                tr("stochastic_form.invalid_mult_msg"),
            )
            return

        # Show selection dialog
        dlg = _MultiplierPickerDialog(available, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected_keys = dlg.selected_keys()
        if not selected_keys:
            return

        # Add selected keys with default value 1.0 to all scenarios
        for sc in self._model.stochastic_scenarios:
            for key in selected_keys:
                sc.multipliers[key] = 1.0

        self.load_element()
        self.scenariosChanged.emit()

    def _on_remove_multiplier(self):
        """Remove the selected multiplier key from all scenarios."""
        row = self._mult_table.currentRow()
        if row < 0 or row >= len(self._mult_keys):
            return

        key = self._mult_keys[row]
        key_to_tr = {k: tr_key for k, tr_key, _ in _COST_MULTIPLIER_CATALOG}
        display = tr(key_to_tr[key]) if key in key_to_tr else key

        # Confirm deletion
        from esfex.visualization.panels._dialogs import confirm_delete
        if not confirm_delete(
            self,
            tr("stochastic_form.confirm_delete_title"),
            tr("stochastic_form.confirm_delete_msg", name=display),
        ):
            return

        # Remove key from all scenarios
        for sc in self._model.stochastic_scenarios:
            sc.multipliers.pop(key, None)

        self.load_element()
        self.scenariosChanged.emit()

    def _on_main_changed(self, row: int, col: int):
        """Handle changes to the main scenario table."""
        if self._updating:
            return
        if row >= len(self._model.stochastic_scenarios):
            return

        sc = self._model.stochastic_scenarios[row]
        item = self._main_table.item(row, col)
        if not item:
            return

        text = item.text()
        if col == 0:  # Name
            # Check for duplicate
            for other_sc in self._model.stochastic_scenarios:
                if other_sc is not sc and other_sc.name == text:
                    QMessageBox.warning(
                        self,
                        tr("messages.duplicate_name_title"),
                        tr("messages.duplicate_name", name=text),
                    )
                    # Revert to old name
                    self._updating = True
                    item.setText(sc.name)
                    self._updating = False
                    return
            sc.name = text
            # Update column headers in multipliers table
            self.load_element()
        elif col == 1:  # Probability
            try:
                sc.probability = float(text)
            except ValueError:
                # Revert to old value
                self._updating = True
                item.setText(f"{sc.probability}")
                self._updating = False
                return
        elif col == 2:  # Description
            sc.description = text

        self.scenariosChanged.emit()

    def _on_mult_changed(self, row: int, col: int):
        """Handle changes to the multipliers table (transposed)."""
        if self._updating:
            return
        if col >= len(self._model.stochastic_scenarios):
            return
        if row >= len(self._mult_keys):
            return

        key = self._mult_keys[row]
        sc = self._model.stochastic_scenarios[col]
        item = self._mult_table.item(row, col)

        if not item:
            return

        try:
            val = float(item.text())
        except ValueError:
            # Revert to old value
            self._updating = True
            old_val = sc.multipliers.get(key, 1.0)
            item.setText(f"{old_val}")
            self._updating = False
            return

        sc.multipliers[key] = val
        self.scenariosChanged.emit()


class _MultiplierPickerDialog(QDialog):
    """Dialog to pick cost multiplier(s) from the catalog."""

    def __init__(
        self,
        available: list[tuple[str, str, str]],
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("stochastic_form.select_multipliers_title"))
        self.setMinimumWidth(460)
        self.setMinimumHeight(420)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(tr("stochastic_form.select_multipliers_msg")))

        # Filter
        self._filter = QLineEdit()
        self._filter.setPlaceholderText(tr("stochastic_form.filter_placeholder"))
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._apply_filter_debounced)
        self._filter.textChanged.connect(lambda _: self._filter_timer.start())
        layout.addWidget(self._filter)

        # List with checkboxes, grouped by category
        self._list = QListWidget()
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )

        self._items: list[tuple[str, QListWidgetItem]] = []  # (key, item)

        current_group = ""
        for key, tr_key, group_tr_key in available:
            # Group header
            label = tr(tr_key)
            group = tr(group_tr_key)
            if group_tr_key != current_group:
                current_group = group_tr_key
                header = QListWidgetItem(f"── {group} ──")
                header.setFlags(header.flags() & ~header.flags())  # not selectable/checkable
                font = header.font()
                font.setBold(True)
                header.setFont(font)
                self._list.addItem(header)
                self._items.append(("", header))  # empty key = header

            item = QListWidgetItem(f"  {label}")
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(256, key)  # store key in UserRole
            item.setToolTip(key)
            self._list.addItem(item)
            self._items.append((key, item))

        layout.addWidget(self._list)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply_filter_debounced(self):
        text_lower = self._filter.text().lower()
        last_header = None
        has_visible_child = False
        for key, item in self._items:
            if not key:
                # Flush previous header visibility
                if last_header is not None:
                    last_header.setHidden(not has_visible_child)
                last_header = item
                has_visible_child = False
                continue
            visible = text_lower in item.text().lower() or text_lower in key.lower()
            item.setHidden(not visible)
            if visible:
                has_visible_child = True
        if last_header is not None:
            last_header.setHidden(not has_visible_child)

    def selected_keys(self) -> list[str]:
        result = []
        for key, item in self._items:
            if key and item.checkState() == Qt.CheckState.Checked:
                result.append(key)
        return result
