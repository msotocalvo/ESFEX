"""Property editor for a single battery/storage instance."""

from __future__ import annotations

from PySide6.QtCore import Signal
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
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import GuiModel
from esfex.visualization.i18n import tr
from esfex.visualization.panels.visual_style_widget import VisualStyleWidget


class BatteryForm(QWidget):
    """Vertical form for editing a :class:`GuiBatteryInstance`."""

    batteryChanged = Signal(str)  # instance_id

    def __init__(self, model: GuiModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._current_id: str | None = None
        self._multi_ids: list[str] | None = None
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._system_label = QLabel("")
        self._system_label.setObjectName("headerLabel")
        layout.addWidget(self._system_label)

        # --- Identification ---
        grp_id = QGroupBox(tr("battery_form.group_id"))
        fl_id = QFormLayout(grp_id)
        self._name = QLineEdit()
        self._name.editingFinished.connect(self._on_changed)
        fl_id.addRow(tr("battery_form.name"), self._name)

        self._fuel = QComboBox()
        self._fuel.currentIndexChanged.connect(self._on_changed)
        fl_id.addRow(tr("battery_form.fuel"), self._fuel)

        self._tech_combo = QComboBox()
        self._tech_combo.addItem(tr("battery_form.none_tech"), "")
        self._tech_combo.currentIndexChanged.connect(self._on_changed)
        fl_id.addRow(tr("battery_form.technology"), self._tech_combo)

        self._node_combo = QComboBox()
        self._node_combo.currentIndexChanged.connect(self._on_changed)
        fl_id.addRow(tr("battery_form.node"), self._node_combo)

        self._availability = QLineEdit()
        self._availability.setPlaceholderText(tr("battery_form.availability_placeholder"))
        self._availability.editingFinished.connect(self._on_changed)
        fl_id.addRow(tr("battery_form.availability"), self._availability)

        self._current_type = QComboBox()
        self._current_type.addItems(["AC", "DC"])
        self._current_type.currentIndexChanged.connect(self._on_changed)
        fl_id.addRow(tr("battery_form.current_type"), self._current_type)

        layout.addWidget(grp_id)

        # --- Power & Capacity ---
        grp_cap = QGroupBox(tr("battery_form.group_power"))
        fl_cap = QFormLayout(grp_cap)
        self._rated_power = self._dbl_spin(0, 1e7, 3)
        fl_cap.addRow(tr("battery_form.rated_power"), self._rated_power)
        self._capacity = self._dbl_spin(0, 1e8, 3)
        fl_cap.addRow(tr("battery_form.capacity"), self._capacity)
        self._MaxChargePower = self._dbl_spin(0, 1e7, 3)
        fl_cap.addRow(tr("battery_form.max_charge"), self._MaxChargePower)
        self._MaxDischargePower = self._dbl_spin(0, 1e7, 3)
        fl_cap.addRow(tr("battery_form.max_discharge"), self._MaxDischargePower)
        self._min_duration = self._int_spin(0, 168)
        fl_cap.addRow(tr("battery_form.min_duration"), self._min_duration)
        self._max_duration = self._int_spin(0, 168)
        fl_cap.addRow(tr("battery_form.max_duration"), self._max_duration)
        layout.addWidget(grp_cap)

        # --- Efficiency & SOC ---
        grp_eff = QGroupBox(tr("battery_form.group_efficiency"))
        fl_eff = QFormLayout(grp_eff)
        self._efficiency_charge = self._dbl_spin(0, 1, 3)
        fl_eff.addRow(tr("battery_form.charge_eff"), self._efficiency_charge)
        self._efficiency_discharge = self._dbl_spin(0, 1, 3)
        fl_eff.addRow(tr("battery_form.discharge_eff"), self._efficiency_discharge)
        self._soc_initial = self._dbl_spin(0, 1, 3)
        fl_eff.addRow(tr("battery_form.soc_initial"), self._soc_initial)
        self._max_DoD = self._dbl_spin(0, 1, 3)
        fl_eff.addRow(tr("battery_form.max_dod"), self._max_DoD)
        layout.addWidget(grp_eff)

        # --- Lifetime ---
        grp_life = QGroupBox(tr("battery_form.group_lifetime"))
        fl_life = QFormLayout(grp_life)
        self._life_time = self._int_spin(0, 100)
        fl_life.addRow(tr("battery_form.life_time"), self._life_time)
        self._initial_age = self._int_spin(0, 100)
        fl_life.addRow(tr("battery_form.initial_age"), self._initial_age)
        self._degradation_rate = self._dbl_spin(0, 1, 4)
        fl_life.addRow(tr("battery_form.degradation"), self._degradation_rate)
        self._risk_coefficient = self._dbl_spin(0, 1, 4)
        self._risk_coefficient.setValue(1.0)
        self._risk_coefficient.setToolTip(
            "Geographic risk derating factor (0-1). Computed from hazard exposure "
            "and component fragility via the Risk Workbench. 1.0 = no derating."
        )
        fl_life.addRow(tr("battery_form.risk_coefficient"), self._risk_coefficient)
        layout.addWidget(grp_life)

        # --- Operating Parameters ---
        grp_op = QGroupBox(tr("battery_form.group_operating"))
        fl_op = QFormLayout(grp_op)
        self._min_power = self._dbl_spin(0, 1, 3)
        fl_op.addRow(tr("battery_form.min_power"), self._min_power)
        self._min_up = self._int_spin(0, 168)
        fl_op.addRow(tr("battery_form.min_up"), self._min_up)
        self._min_down = self._int_spin(0, 168)
        fl_op.addRow(tr("battery_form.min_down"), self._min_down)
        self._ramp_up = self._dbl_spin(0, 10, 3)
        fl_op.addRow(tr("battery_form.ramp_up"), self._ramp_up)
        self._ramp_down = self._dbl_spin(0, 10, 3)
        fl_op.addRow(tr("battery_form.ramp_down"), self._ramp_down)
        self._eff_at_rated = self._dbl_spin(0, 1, 3)
        fl_op.addRow(tr("battery_form.eff_at_rated"), self._eff_at_rated)
        self._eff_at_min = self._dbl_spin(0, 1, 3)
        fl_op.addRow(tr("battery_form.eff_at_min"), self._eff_at_min)
        self._inertia = self._dbl_spin(0, 100, 2)
        fl_op.addRow(tr("battery_form.inertia"), self._inertia)
        layout.addWidget(grp_op)

        # --- Costs ---
        grp_cost = QGroupBox(tr("battery_form.group_costs"))
        fl_cost = QFormLayout(grp_cost)
        self._fuel_cost = self._dbl_spin(0, 1e6, 2)
        fl_cost.addRow(tr("battery_form.fuel_cost"), self._fuel_cost)
        self._fixed_cost = self._dbl_spin(0, 1e6, 2)
        fl_cost.addRow(tr("battery_form.fixed_cost"), self._fixed_cost)
        self._maintenance_cost = self._dbl_spin(0, 1e6, 2)
        fl_cost.addRow(tr("battery_form.maintenance_cost"), self._maintenance_cost)
        self._throughput_degradation_cost = self._dbl_spin(0, 1e6, 2)
        self._throughput_degradation_cost.setToolTip(
            tr("battery_form.throughput_degradation_tooltip"))
        fl_cost.addRow(tr("battery_form.throughput_degradation_cost"), self._throughput_degradation_cost)

        # Discharge cost curve type selector
        self._dc_curve_type = QComboBox()
        self._dc_curve_type.addItems(["flat", "linear", "stepwise", "exponential"])
        self._dc_curve_type.currentIndexChanged.connect(self._on_dc_curve_type_changed)
        fl_cost.addRow(tr("battery_form.discharge_curve_type"), self._dc_curve_type)

        self._dc_curve_stack = QStackedWidget()

        # Page 0: Flat
        self._dc_curve_stack.addWidget(QWidget())

        # Page 1: Linear
        page_lin = QWidget()
        fl_lin = QFormLayout(page_lin)
        fl_lin.setContentsMargins(0, 0, 0, 0)
        self._dc_price_at_zero = self._dbl_spin(0, 1e6, 2)
        fl_lin.addRow(tr("battery_form.price_at_zero"), self._dc_price_at_zero)
        self._dc_price_at_max = self._dbl_spin(0, 1e6, 2)
        fl_lin.addRow(tr("battery_form.price_at_max"), self._dc_price_at_max)
        self._dc_num_segments = self._int_spin(2, 20)
        self._dc_num_segments.setValue(5)
        fl_lin.addRow(tr("battery_form.num_segments"), self._dc_num_segments)
        self._dc_curve_stack.addWidget(page_lin)

        # Page 2: Stepwise
        page_step = QWidget()
        vl_step = QVBoxLayout(page_step)
        vl_step.setContentsMargins(0, 0, 0, 0)
        self._dc_blocks_table = QTableWidget(0, 2)
        self._dc_blocks_table.setHorizontalHeaderLabels(
            [tr("battery_form.fraction"), tr("battery_form.price_mwh")]
        )
        self._dc_blocks_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._dc_blocks_table.setMaximumHeight(150)
        self._dc_blocks_table.cellChanged.connect(self._on_changed)
        vl_step.addWidget(self._dc_blocks_table)
        btn_row = QHBoxLayout()
        btn_add = QPushButton(tr("battery_form.add_block"))
        btn_add.clicked.connect(lambda: self._add_block_row(self._dc_blocks_table))
        btn_rm = QPushButton(tr("battery_form.remove_block"))
        btn_rm.clicked.connect(lambda: self._remove_block_row(self._dc_blocks_table))
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_rm)
        vl_step.addLayout(btn_row)
        self._dc_curve_stack.addWidget(page_step)

        # Page 3: Exponential
        page_exp = QWidget()
        fl_exp = QFormLayout(page_exp)
        fl_exp.setContentsMargins(0, 0, 0, 0)
        self._dc_base_price = self._dbl_spin(0, 1e6, 2)
        fl_exp.addRow(tr("battery_form.base_price"), self._dc_base_price)
        self._dc_scale_factor = self._dbl_spin(0, 10, 3)
        fl_exp.addRow(tr("battery_form.scale_factor"), self._dc_scale_factor)
        self._dc_exp_segments = self._int_spin(2, 20)
        self._dc_exp_segments.setValue(5)
        fl_exp.addRow(tr("battery_form.num_segments"), self._dc_exp_segments)
        self._dc_curve_stack.addWidget(page_exp)

        self._dc_curve_stack.setCurrentIndex(0)
        self._dc_curve_stack.setVisible(False)
        fl_cost.addRow(self._dc_curve_stack)

        self._start_up_cost = self._dbl_spin(0, 1e9, 0)
        fl_cost.addRow(tr("battery_form.startup_cost"), self._start_up_cost)
        self._decommissioning_cost = self._dbl_spin(0, 1e9, 0)
        fl_cost.addRow(tr("battery_form.decommissioning_cost"), self._decommissioning_cost)
        layout.addWidget(grp_cost)

        # --- Appearance ---
        self._style_widget = VisualStyleWidget(
            show_color=True, show_size=True,
        )
        self._style_widget.styleChanged.connect(self._on_changed)
        layout.addWidget(self._style_widget)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def load_element(self, element_id: str):
        inst = self._model.state.batteries.get(element_id)
        if not inst:
            return
        self._multi_ids = None
        self._current_id = element_id
        self._system_label.setText(tr("battery_form.system_label", name=self._model.state.name))
        self._updating = True

        # Propagate electrical properties from bus
        self._model.propagate_bus_to_element("battery", element_id)

        # Node combo
        self._node_combo.blockSignals(True)
        self._node_combo.clear()
        for nd in self._model.state.nodes:
            self._node_combo.addItem(tr("battery_form.node_combo_fmt", idx=nd.index, name=nd.name), nd.index)
        idx = self._node_combo.findData(inst.node)
        if idx >= 0:
            self._node_combo.setCurrentIndex(idx)
        self._node_combo.blockSignals(False)

        self._name.setText(inst.name)

        # Populate fuel combo from system fuels
        self._fuel.blockSignals(True)
        self._fuel.clear()
        for fuel in self._model.state.fuels.values():
            self._fuel.addItem(fuel.name)
        fuel_idx = self._fuel.findText(inst.fuel)
        if fuel_idx < 0 and inst.fuel:
            self._fuel.addItem(inst.fuel)
            fuel_idx = self._fuel.count() - 1
        if fuel_idx >= 0:
            self._fuel.setCurrentIndex(fuel_idx)
        self._fuel.blockSignals(False)

        # Populate technology combo with Storage techs
        self._tech_combo.blockSignals(True)
        self._tech_combo.clear()
        self._tech_combo.addItem(tr("battery_form.none_tech"), "")
        for tech in self._model.state.technologies.values():
            if tech.category == "Storage":
                self._tech_combo.addItem(tech.name, tech.tech_id)
        tech_idx = self._tech_combo.findData(inst.technology_id or "")
        if tech_idx >= 0:
            self._tech_combo.setCurrentIndex(tech_idx)
        self._tech_combo.blockSignals(False)

        self._availability.setText(inst.availability_file or "")

        ct_idx = self._current_type.findText(getattr(inst, 'current_type', 'DC'))
        if ct_idx >= 0:
            self._current_type.setCurrentIndex(ct_idx)

        self._rated_power.setValue(inst.rated_power)
        self._capacity.setValue(inst.capacity)
        self._MaxChargePower.setValue(inst.MaxChargePower)
        self._MaxDischargePower.setValue(inst.MaxDischargePower)
        self._min_duration.setValue(inst.min_duration_hours or 0)
        self._max_duration.setValue(inst.max_duration_hours or 0)

        self._efficiency_charge.setValue(inst.efficiency_charge)
        self._efficiency_discharge.setValue(inst.efficiency_discharge)
        self._soc_initial.setValue(inst.soc_initial)
        self._max_DoD.setValue(inst.max_DoD)

        self._life_time.setValue(inst.life_time)
        self._initial_age.setValue(inst.initial_age)
        self._degradation_rate.setValue(inst.degradation_rate)
        self._risk_coefficient.setValue(getattr(inst, "risk_coefficient", 1.0))

        self._min_power.setValue(inst.min_power)
        self._min_up.setValue(inst.min_up)
        self._min_down.setValue(inst.min_down)
        self._ramp_up.setValue(inst.ramp_up)
        self._ramp_down.setValue(inst.ramp_down)
        self._eff_at_rated.setValue(inst.eff_at_rated)
        self._eff_at_min.setValue(inst.eff_at_min)
        self._inertia.setValue(inst.inertia)

        self._fuel_cost.setValue(inst.fuel_cost)
        self._fixed_cost.setValue(inst.fixed_cost)
        self._maintenance_cost.setValue(inst.maintenance_cost)
        self._throughput_degradation_cost.setValue(inst.throughput_degradation_cost)

        # Load discharge cost curve
        dct = getattr(inst, 'discharge_cost_curve_type', 'flat') or 'flat'
        dct_idx = self._dc_curve_type.findText(dct)
        if dct_idx >= 0:
            self._dc_curve_type.setCurrentIndex(dct_idx)
        self._load_dc_curve_data(getattr(inst, 'discharge_cost_curve_data', None) or {}, dct)

        self._start_up_cost.setValue(inst.start_up_cost)
        self._decommissioning_cost.setValue(inst.decommissioning_cost)

        from esfex.visualization.data.default_colors import BATTERY
        self._style_widget.set_default_color(BATTERY)
        self._style_widget.load_style(inst.style)
        self._updating = False

    def load_elements(self, element_ids: list[str]):
        """Load multiple batteries for batch editing."""
        from esfex.visualization.panels.multi_edit import collect_attr, set_widget_value

        instances = [self._model.state.batteries[eid] for eid in element_ids
                     if eid in self._model.state.batteries]
        if not instances:
            return
        self._multi_ids = element_ids
        self._current_id = element_ids[0]
        self._system_label.setText(tr("battery_form.n_selected", n=len(element_ids)))
        self._updating = True

        self._node_combo.blockSignals(True)
        self._node_combo.clear()
        for nd in self._model.state.nodes:
            self._node_combo.addItem(tr("battery_form.node_combo_fmt", idx=nd.index, name=nd.name), nd.index)
        self._node_combo.blockSignals(False)
        set_widget_value(self._node_combo, collect_attr(instances, "node"))

        for attr, w in self._field_map():
            set_widget_value(w, collect_attr(instances, attr))

        self._updating = False

    def _field_map(self):
        """Return (model_attr, widget) pairs for simple fields."""
        return [
            ("name", self._name),
            ("fuel", self._fuel),
            ("availability_file", self._availability),
            ("current_type", self._current_type),
            ("rated_power", self._rated_power),
            ("capacity", self._capacity),
            ("MaxChargePower", self._MaxChargePower),
            ("MaxDischargePower", self._MaxDischargePower),
            ("min_duration_hours", self._min_duration),
            ("max_duration_hours", self._max_duration),
            ("efficiency_charge", self._efficiency_charge),
            ("efficiency_discharge", self._efficiency_discharge),
            ("soc_initial", self._soc_initial),
            ("max_DoD", self._max_DoD),
            ("life_time", self._life_time),
            ("initial_age", self._initial_age),
            ("degradation_rate", self._degradation_rate),
            ("risk_coefficient", self._risk_coefficient),
            ("min_power", self._min_power),
            ("min_up", self._min_up),
            ("min_down", self._min_down),
            ("ramp_up", self._ramp_up),
            ("ramp_down", self._ramp_down),
            ("eff_at_rated", self._eff_at_rated),
            ("eff_at_min", self._eff_at_min),
            ("inertia", self._inertia),
            ("fuel_cost", self._fuel_cost),
            ("fixed_cost", self._fixed_cost),
            ("maintenance_cost", self._maintenance_cost),
            ("throughput_degradation_cost", self._throughput_degradation_cost),
            ("start_up_cost", self._start_up_cost),
            ("decommissioning_cost", self._decommissioning_cost),
        ]

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_changed(self):
        if self._updating:
            return
        self._model.checkpoint()
        from esfex.visualization.panels.multi_edit import widget_is_mixed

        ids = self._multi_ids or ([self._current_id] if self._current_id else [])
        instances = [self._model.state.batteries[i] for i in ids
                     if i in self._model.state.batteries]
        if not instances:
            return

        for attr, w in self._field_map():
            if widget_is_mixed(w):
                continue
            if isinstance(w, QLineEdit):
                val = w.text()
                if attr == "availability_file":
                    val = val or None
                elif attr in ("min_duration_hours", "max_duration_hours"):
                    pass  # handled below
            elif isinstance(w, QComboBox):
                val = w.currentText()
            else:
                val = w.value()
            if attr == "min_duration_hours":
                val = self._min_duration.value() or None
            elif attr == "max_duration_hours":
                val = self._max_duration.value() or None
            for inst in instances:
                setattr(inst, attr, val)

        if not widget_is_mixed(self._node_combo):
            nd = self._node_combo.currentData()
            if nd is not None:
                for inst in instances:
                    inst.node = nd

        if not widget_is_mixed(self._tech_combo):
            tech_id = self._tech_combo.currentData() or None
            for inst in instances:
                inst.technology_id = tech_id

        # Sync discharge cost curve
        dct = self._dc_curve_type.currentText()
        dc_data = self._collect_dc_curve_data(dct)
        for inst in instances:
            inst.discharge_cost_curve_type = dct
            inst.discharge_cost_curve_data = dc_data

        if not self._multi_ids:
            instances[0].style = self._style_widget.get_style()

        if len(ids) > 3:
            self._model.stateLoaded.emit()
        else:
            for eid in ids:
                self._model.batteryUpdated.emit(eid)
                self.batteryChanged.emit(eid)

    def _on_dc_curve_type_changed(self, idx):
        """Show/hide discharge cost curve stack."""
        self._dc_curve_stack.setCurrentIndex(idx)
        self._dc_curve_stack.setVisible(idx > 0)
        self._on_changed()

    # ------------------------------------------------------------------
    # Discharge cost curve helpers
    # ------------------------------------------------------------------

    def _load_dc_curve_data(self, data: dict, curve_type: str):
        """Populate discharge curve widgets from stored data."""
        if curve_type == "linear":
            self._dc_price_at_zero.setValue(data.get("price_at_zero", 0.0))
            self._dc_price_at_max.setValue(data.get("price_at_max", 0.0))
            self._dc_num_segments.setValue(data.get("num_segments", 5))
        elif curve_type == "stepwise":
            blocks = data.get("blocks", [])
            self._dc_blocks_table.blockSignals(True)
            self._dc_blocks_table.setRowCount(len(blocks))
            for r, b in enumerate(blocks):
                self._dc_blocks_table.setItem(r, 0, QTableWidgetItem(str(b.get("fraction", 0.0))))
                self._dc_blocks_table.setItem(r, 1, QTableWidgetItem(str(b.get("price", 0.0))))
            self._dc_blocks_table.blockSignals(False)
        elif curve_type == "exponential":
            self._dc_base_price.setValue(data.get("base_price", 0.0))
            self._dc_scale_factor.setValue(data.get("scale_factor", 1.0))
            self._dc_exp_segments.setValue(data.get("num_segments", 5))
        page_idx = {"flat": 0, "linear": 1, "stepwise": 2, "exponential": 3}.get(curve_type, 0)
        self._dc_curve_stack.setCurrentIndex(page_idx)
        self._dc_curve_stack.setVisible(page_idx > 0)

    def _collect_dc_curve_data(self, curve_type: str) -> dict | None:
        """Gather discharge curve parameters from widgets."""
        if curve_type == "flat":
            return None
        if curve_type == "linear":
            return {
                "price_at_zero": self._dc_price_at_zero.value(),
                "price_at_max": self._dc_price_at_max.value(),
                "num_segments": self._dc_num_segments.value(),
            }
        if curve_type == "stepwise":
            blocks = []
            for r in range(self._dc_blocks_table.rowCount()):
                frac_item = self._dc_blocks_table.item(r, 0)
                price_item = self._dc_blocks_table.item(r, 1)
                try:
                    frac = float(frac_item.text()) if frac_item else 0.0
                    price = float(price_item.text()) if price_item else 0.0
                except ValueError:
                    continue
                blocks.append({"fraction": frac, "price": price})
            return {"blocks": blocks}
        if curve_type == "exponential":
            return {
                "base_price": self._dc_base_price.value(),
                "scale_factor": self._dc_scale_factor.value(),
                "num_segments": self._dc_exp_segments.value(),
            }
        return None

    def _add_block_row(self, table: QTableWidget):
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem("0.2"))
        table.setItem(row, 1, QTableWidgetItem("0.0"))
        self._on_changed()

    def _remove_block_row(self, table: QTableWidget):
        if table.rowCount() > 0:
            table.removeRow(table.rowCount() - 1)
            self._on_changed()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _dbl_spin(self, lo: float, hi: float, decimals: int = 2) -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setDecimals(decimals)
        sb.valueChanged.connect(self._on_changed)
        return sb

    def _int_spin(self, lo: int, hi: int) -> QSpinBox:
        sb = QSpinBox()
        sb.setRange(lo, hi)
        sb.valueChanged.connect(self._on_changed)
        return sb
