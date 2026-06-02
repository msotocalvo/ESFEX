"""Property editor for a single generator instance."""

from __future__ import annotations

from PySide6.QtCore import Qt, QEvent, Signal
from PySide6.QtWidgets import (
    QCheckBox,
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


class GeneratorForm(QWidget):
    """Vertical form for editing a :class:`GuiGeneratorInstance`."""

    generatorChanged = Signal(str)  # instance_id

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
        grp_id = QGroupBox(tr("generator_form.group_id"))
        fl_id = QFormLayout(grp_id)
        self._name = QLineEdit()
        self._name.editingFinished.connect(self._on_changed)
        fl_id.addRow(tr("generator_form.name"), self._name)

        self._gen_type = QComboBox()
        self._gen_type.addItems([tr("generator_form.type_renewable"), tr("generator_form.type_nonrenewable")])
        self._gen_type.currentIndexChanged.connect(self._on_changed)
        fl_id.addRow(tr("generator_form.type"), self._gen_type)

        self._fuel = QComboBox()
        self._fuel.currentIndexChanged.connect(self._on_changed)
        fl_id.addRow(tr("generator_form.fuel"), self._fuel)

        self._tech_combo = QComboBox()
        self._tech_combo.addItem(tr("generator_form.none_tech"), "")
        self._tech_combo.currentIndexChanged.connect(self._on_changed)
        fl_id.addRow(tr("generator_form.technology"), self._tech_combo)

        self._node_combo = QComboBox()
        self._node_combo.currentIndexChanged.connect(self._on_changed)
        fl_id.addRow(tr("generator_form.node"), self._node_combo)

        # Availability CSV (hourly profile, 0..1).  A compact
        # QToolButton parented INSIDE the QLineEdit, positioned at
        # its right edge — same visual placement as the native clear
        # button.  Toggles between "..." (browse) when the field is
        # empty and "×" (clear) when a path is set.
        from PySide6.QtWidgets import QToolButton
        self._availability = QLineEdit()
        self._availability.setPlaceholderText(tr("generator_form.availability_placeholder"))
        self._availability.editingFinished.connect(self._on_changed)
        self._availability_btn = QToolButton(self._availability)
        self._availability_btn.setCursor(Qt.CursorShape.ArrowCursor)
        self._availability_btn.setStyleSheet(
            "QToolButton { border: none; padding: 0; background: transparent; }"
            "QToolButton:hover { background: rgba(127, 127, 127, 0.18); "
            "border-radius: 3px; }"
        )
        self._availability_btn.setFixedSize(18, 18)
        self._availability_btn.clicked.connect(
            self._on_availability_action_triggered
        )
        # Reserve right padding so the text never runs under the button.
        self._availability.setTextMargins(0, 0, 22, 0)
        self._availability.installEventFilter(self)
        self._availability.textChanged.connect(self._refresh_availability_action)
        self._refresh_availability_action(self._availability.text())
        fl_id.addRow(tr("generator_form.availability"), self._availability)

        layout.addWidget(grp_id)

        # --- Capacity ---
        grp_cap = QGroupBox(tr("generator_form.group_capacity"))
        fl_cap = QFormLayout(grp_cap)
        self._rated_power = self._dbl_spin(0, 1e7, 3)
        fl_cap.addRow(tr("generator_form.rated_power"), self._rated_power)
        self._life_time = self._int_spin(0, 100)
        fl_cap.addRow(tr("generator_form.life_time"), self._life_time)
        self._initial_age = self._int_spin(0, 100)
        fl_cap.addRow(tr("generator_form.initial_age"), self._initial_age)
        self._degradation_rate = self._dbl_spin(0, 1, 4)
        fl_cap.addRow(tr("generator_form.degradation"), self._degradation_rate)
        self._risk_coefficient = self._dbl_spin(0, 1, 4)
        self._risk_coefficient.setValue(1.0)
        self._risk_coefficient.setToolTip(
            "Geographic risk derating factor (0-1). Computed from hazard exposure "
            "and component fragility via the Risk Workbench. 1.0 = no derating."
        )
        fl_cap.addRow(tr("generator_form.risk_coefficient"), self._risk_coefficient)
        layout.addWidget(grp_cap)

        # --- Operating Parameters ---
        grp_op = QGroupBox(tr("generator_form.group_operating"))
        fl_op = QFormLayout(grp_op)
        self._min_power = self._dbl_spin(0, 1, 3)
        fl_op.addRow(tr("generator_form.min_power"), self._min_power)
        self._min_up = self._int_spin(0, 168)
        fl_op.addRow(tr("generator_form.min_up"), self._min_up)
        self._min_down = self._int_spin(0, 168)
        fl_op.addRow(tr("generator_form.min_down"), self._min_down)
        self._ramp_up = self._dbl_spin(0, 10, 3)
        fl_op.addRow(tr("generator_form.ramp_up"), self._ramp_up)
        self._ramp_down = self._dbl_spin(0, 10, 3)
        fl_op.addRow(tr("generator_form.ramp_down"), self._ramp_down)
        self._eff_at_rated = self._dbl_spin(0, 1, 3)
        fl_op.addRow(tr("generator_form.eff_at_rated"), self._eff_at_rated)
        self._eff_at_min = self._dbl_spin(0, 1, 3)
        fl_op.addRow(tr("generator_form.eff_at_min"), self._eff_at_min)
        layout.addWidget(grp_op)

        # --- Costs ---
        grp_cost = QGroupBox(tr("generator_form.group_costs"))
        fl_cost = QFormLayout(grp_cost)

        # Fuel cost: flat spinbox + curve type selector
        self._fuel_cost = self._dbl_spin(0, 1e6, 2)
        fl_cost.addRow(tr("generator_form.fuel_cost"), self._fuel_cost)

        self._fuel_curve_type = QComboBox()
        self._fuel_curve_type.addItems(["flat", "linear", "stepwise", "exponential"])
        self._fuel_curve_type.currentIndexChanged.connect(self._on_fuel_curve_type_changed)
        fl_cost.addRow(tr("generator_form.curve_type"), self._fuel_curve_type)

        # Stacked widget for curve-specific parameters
        self._fuel_curve_stack = QStackedWidget()

        # Page 0: Flat — no extra widgets (fuel_cost spinbox is already shown)
        self._fuel_curve_stack.addWidget(QWidget())

        # Page 1: Linear — price_at_zero + price_at_max + num_segments
        page_linear = QWidget()
        fl_lin = QFormLayout(page_linear)
        fl_lin.setContentsMargins(0, 0, 0, 0)
        self._fc_price_at_zero = self._dbl_spin(0, 1e6, 2)
        fl_lin.addRow(tr("generator_form.price_at_zero"), self._fc_price_at_zero)
        self._fc_price_at_max = self._dbl_spin(0, 1e6, 2)
        fl_lin.addRow(tr("generator_form.price_at_max"), self._fc_price_at_max)
        self._fc_num_segments = self._int_spin(2, 20)
        self._fc_num_segments.setValue(5)
        fl_lin.addRow(tr("generator_form.num_segments"), self._fc_num_segments)
        self._fuel_curve_stack.addWidget(page_linear)

        # Page 2: Stepwise — table + add/remove buttons
        page_step = QWidget()
        vl_step = QVBoxLayout(page_step)
        vl_step.setContentsMargins(0, 0, 0, 0)
        self._fc_blocks_table = QTableWidget(0, 2)
        self._fc_blocks_table.setHorizontalHeaderLabels(
            [tr("generator_form.fraction"), tr("generator_form.price_mwh")]
        )
        self._fc_blocks_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._fc_blocks_table.setMaximumHeight(150)
        self._fc_blocks_table.cellChanged.connect(self._on_changed)
        vl_step.addWidget(self._fc_blocks_table)
        btn_row = QHBoxLayout()
        btn_add = QPushButton(tr("generator_form.add_block"))
        btn_add.clicked.connect(lambda: self._add_block_row(self._fc_blocks_table))
        btn_rm = QPushButton(tr("generator_form.remove_block"))
        btn_rm.clicked.connect(lambda: self._remove_block_row(self._fc_blocks_table))
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_rm)
        vl_step.addLayout(btn_row)
        self._fuel_curve_stack.addWidget(page_step)

        # Page 3: Exponential — base_price + scale_factor + num_segments
        page_exp = QWidget()
        fl_exp = QFormLayout(page_exp)
        fl_exp.setContentsMargins(0, 0, 0, 0)
        self._fc_base_price = self._dbl_spin(0, 1e6, 2)
        fl_exp.addRow(tr("generator_form.base_price"), self._fc_base_price)
        self._fc_scale_factor = self._dbl_spin(0, 10, 3)
        fl_exp.addRow(tr("generator_form.scale_factor"), self._fc_scale_factor)
        self._fc_exp_segments = self._int_spin(2, 20)
        self._fc_exp_segments.setValue(5)
        fl_exp.addRow(tr("generator_form.num_segments"), self._fc_exp_segments)
        self._fuel_curve_stack.addWidget(page_exp)

        self._fuel_curve_stack.setCurrentIndex(0)
        self._fuel_curve_stack.setVisible(False)  # Hidden for "flat"
        fl_cost.addRow(self._fuel_curve_stack)

        self._fixed_cost = self._dbl_spin(0, 1e6, 2)
        fl_cost.addRow(tr("generator_form.fixed_cost"), self._fixed_cost)
        self._maintenance_cost = self._dbl_spin(0, 1e6, 2)
        fl_cost.addRow(tr("generator_form.maintenance_cost"), self._maintenance_cost)
        self._start_up_cost = self._dbl_spin(0, 1e9, 0)
        fl_cost.addRow(tr("generator_form.startup_cost"), self._start_up_cost)
        self._decommissioning_cost = self._dbl_spin(0, 1e9, 0)
        fl_cost.addRow(tr("generator_form.decommissioning_cost"), self._decommissioning_cost)
        layout.addWidget(grp_cost)

        # --- Electrical ---
        grp_elec = QGroupBox(tr("generator_form.group_electrical"))
        fl_elec = QFormLayout(grp_elec)
        self._frequency_hz = self._dbl_spin(0.1, 400, 1)
        fl_elec.addRow(tr("generator_form.frequency"), self._frequency_hz)
        self._current_type = QComboBox()
        self._current_type.addItems(["AC", "DC", "AC_DC"])
        self._current_type.currentIndexChanged.connect(self._on_changed)
        fl_elec.addRow(tr("generator_form.current_type"), self._current_type)
        self._inertia = self._dbl_spin(0, 100, 2)
        fl_elec.addRow(tr("generator_form.inertia"), self._inertia)
        self._droop = self._dbl_spin(0, 1, 3)
        self._droop.setSuffix(" pu")
        fl_elec.addRow(tr("generator_form.droop"), self._droop)
        self._governor_time_const = self._dbl_spin(0, 100, 3)
        self._governor_time_const.setSuffix(" s")
        fl_elec.addRow(tr("generator_form.governor_time_const"), self._governor_time_const)
        layout.addWidget(grp_elec)

        # --- Reservoir ---
        self._reservoir_group = QGroupBox(tr("generator_form.group_reservoir"))
        fl_res = QFormLayout(self._reservoir_group)

        self._reservoir_capacity = self._dbl_spin(0, 1e6, 3)
        fl_res.addRow(tr("generator_form.reservoir_capacity"), self._reservoir_capacity)

        self._reservoir_initial_level = self._dbl_spin(0.0, 1.0, 2)
        fl_res.addRow(tr("generator_form.reservoir_initial_level"), self._reservoir_initial_level)

        self._reservoir_min_level = self._dbl_spin(0.0, 1.0, 2)
        fl_res.addRow(tr("generator_form.reservoir_min_level"), self._reservoir_min_level)

        self._reservoir_max_level = self._dbl_spin(0.0, 1.0, 2)
        fl_res.addRow(tr("generator_form.reservoir_max_level"), self._reservoir_max_level)

        # Inflow file with browse button
        inflow_layout = QHBoxLayout()
        self._reservoir_inflow_file = QLineEdit()
        self._reservoir_inflow_file.setPlaceholderText(tr("generator_form.reservoir_inflow_placeholder"))
        self._reservoir_inflow_file.editingFinished.connect(self._on_changed)
        inflow_layout.addWidget(self._reservoir_inflow_file)
        btn_inflow = QPushButton("...")
        btn_inflow.setFixedWidth(30)
        btn_inflow.clicked.connect(self._browse_inflow_file)
        inflow_layout.addWidget(btn_inflow)
        fl_res.addRow(tr("generator_form.reservoir_inflow_file"), inflow_layout)

        self._reservoir_turbine_efficiency = self._dbl_spin(0.0, 1.0, 2)
        fl_res.addRow(tr("generator_form.reservoir_turbine_efficiency"), self._reservoir_turbine_efficiency)

        self._reservoir_evaporation_rate = self._dbl_spin(0.0, 0.01, 5)
        fl_res.addRow(tr("generator_form.reservoir_evaporation_rate"), self._reservoir_evaporation_rate)

        self._reservoir_pump_capacity = self._dbl_spin(0, 1e5, 3)
        fl_res.addRow(tr("generator_form.reservoir_pump_capacity"), self._reservoir_pump_capacity)

        self._reservoir_pump_efficiency = self._dbl_spin(0.0, 1.0, 2)
        fl_res.addRow(tr("generator_form.reservoir_pump_efficiency"), self._reservoir_pump_efficiency)

        self._reservoir_spillage_allowed = QCheckBox()
        self._reservoir_spillage_allowed.stateChanged.connect(self._on_changed)
        fl_res.addRow(tr("generator_form.reservoir_spillage_allowed"), self._reservoir_spillage_allowed)

        self._reservoir_invest_cost = self._dbl_spin(0, 1e6, 3)
        fl_res.addRow(tr("generator_form.reservoir_invest_cost"), self._reservoir_invest_cost)

        self._reservoir_invest_max = self._dbl_spin(0, 1e6, 3)
        fl_res.addRow(tr("generator_form.reservoir_invest_max"), self._reservoir_invest_max)

        self._reservoir_group.setVisible(False)
        layout.addWidget(self._reservoir_group)

        # Connect reservoir_capacity to show/hide the group
        # (disconnect first since _dbl_spin already connected _on_changed)
        self._reservoir_capacity.valueChanged.connect(self._on_reservoir_toggled)

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
        inst = self._model.state.generators.get(element_id)
        if not inst:
            return
        self._multi_ids = None
        self._current_id = element_id
        self._system_label.setText(tr("generator_form.system_label", name=self._model.state.name))
        self._updating = True

        # Propagate electrical properties from bus
        self._model.propagate_bus_to_element("generator", element_id)

        # Populate node combo
        self._node_combo.blockSignals(True)
        self._node_combo.clear()
        for nd in self._model.state.nodes:
            self._node_combo.addItem(tr("generator_form.node_combo_fmt", idx=nd.index, name=nd.name), nd.index)
        idx = self._node_combo.findData(inst.node)
        if idx >= 0:
            self._node_combo.setCurrentIndex(idx)
        self._node_combo.blockSignals(False)

        self._name.setText(inst.name)
        self._gen_type.setCurrentText(inst.gen_type)

        # Populate fuel combo from system fuels
        self._fuel.blockSignals(True)
        self._fuel.clear()
        for fuel in self._model.state.fuels.values():
            self._fuel.addItem(fuel.name)
        fuel_idx = self._fuel.findText(inst.fuel)
        if fuel_idx < 0 and inst.fuel:
            # Fuel not in system list — add it so it can be selected
            self._fuel.addItem(inst.fuel)
            fuel_idx = self._fuel.count() - 1
        if fuel_idx >= 0:
            self._fuel.setCurrentIndex(fuel_idx)
        self._fuel.blockSignals(False)

        # Populate technology combo with matching techs
        self._tech_combo.blockSignals(True)
        self._tech_combo.clear()
        self._tech_combo.addItem(tr("generator_form.none_tech"), "")
        for tech in self._model.state.technologies.values():
            if tech.category in ("Renewable", "Non-renewable"):
                self._tech_combo.addItem(tech.name, tech.tech_id)
        tech_idx = self._tech_combo.findData(inst.technology_id or "")
        if tech_idx >= 0:
            self._tech_combo.setCurrentIndex(tech_idx)
        self._tech_combo.blockSignals(False)

        self._availability.setText(inst.availability_file or "")

        self._rated_power.setValue(inst.rated_power)
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

        self._fuel_cost.setValue(inst.fuel_cost)

        # Load fuel cost curve
        ct = inst.fuel_cost_curve_type or "flat"
        ct_idx = self._fuel_curve_type.findText(ct)
        if ct_idx >= 0:
            self._fuel_curve_type.setCurrentIndex(ct_idx)
        self._load_fuel_curve_data(inst.fuel_cost_curve_data or {}, ct)

        self._fixed_cost.setValue(inst.fixed_cost)
        self._maintenance_cost.setValue(inst.maintenance_cost)
        self._start_up_cost.setValue(inst.start_up_cost)
        self._decommissioning_cost.setValue(inst.decommissioning_cost)

        self._frequency_hz.setValue(inst.frequency_hz)
        ct_idx = self._current_type.findText(inst.current_type)
        if ct_idx >= 0:
            self._current_type.setCurrentIndex(ct_idx)
        self._inertia.setValue(inst.inertia)
        self._droop.setValue(inst.droop)
        self._governor_time_const.setValue(inst.governor_time_const)

        # Reservoir fields
        self._reservoir_capacity.setValue(inst.reservoir_capacity)
        self._reservoir_initial_level.setValue(inst.reservoir_initial_level)
        self._reservoir_min_level.setValue(inst.reservoir_min_level)
        self._reservoir_max_level.setValue(inst.reservoir_max_level)
        self._reservoir_inflow_file.setText(inst.reservoir_inflow_file or "")
        self._reservoir_turbine_efficiency.setValue(inst.reservoir_turbine_efficiency)
        self._reservoir_evaporation_rate.setValue(inst.reservoir_evaporation_rate)
        self._reservoir_pump_capacity.setValue(inst.reservoir_pump_capacity)
        self._reservoir_pump_efficiency.setValue(inst.reservoir_pump_efficiency)
        self._reservoir_spillage_allowed.setChecked(inst.reservoir_spillage_allowed)
        self._reservoir_invest_cost.setValue(inst.reservoir_invest_cost)
        self._reservoir_invest_max.setValue(inst.reservoir_invest_max)
        self._reservoir_group.setVisible(inst.reservoir_capacity > 0)

        # Set default color based on generator type before loading style
        from esfex.visualization.data.default_colors import get_generator_color
        self._style_widget.set_default_color(get_generator_color(inst.gen_type))
        self._style_widget.load_style(inst.style)
        self._updating = False

    def load_elements(self, element_ids: list[str]):
        """Load multiple generators for batch editing."""
        from esfex.visualization.panels.multi_edit import collect_attr, set_widget_value

        instances = [self._model.state.generators[eid] for eid in element_ids
                     if eid in self._model.state.generators]
        if not instances:
            return
        self._multi_ids = element_ids
        self._current_id = element_ids[0]
        self._system_label.setText(tr("generator_form.n_selected", n=len(element_ids)))
        self._updating = True

        # Node combo
        self._node_combo.blockSignals(True)
        self._node_combo.clear()
        for nd in self._model.state.nodes:
            self._node_combo.addItem(tr("generator_form.node_combo_fmt", idx=nd.index, name=nd.name), nd.index)
        self._node_combo.blockSignals(False)
        set_widget_value(self._node_combo, collect_attr(instances, "node"))

        for attr, w in self._field_map():
            set_widget_value(w, collect_attr(instances, attr))

        self._updating = False

    def _field_map(self):
        """Return (model_attr, widget) pairs for simple fields."""
        return [
            ("name", self._name),
            ("gen_type", self._gen_type),
            ("fuel", self._fuel),
            ("availability_file", self._availability),
            ("rated_power", self._rated_power),
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
            ("fuel_cost", self._fuel_cost),
            ("fixed_cost", self._fixed_cost),
            ("maintenance_cost", self._maintenance_cost),
            ("start_up_cost", self._start_up_cost),
            ("decommissioning_cost", self._decommissioning_cost),
            ("frequency_hz", self._frequency_hz),
            ("current_type", self._current_type),
            ("inertia", self._inertia),
            ("droop", self._droop),
            ("governor_time_const", self._governor_time_const),
            ("reservoir_capacity", self._reservoir_capacity),
            ("reservoir_initial_level", self._reservoir_initial_level),
            ("reservoir_min_level", self._reservoir_min_level),
            ("reservoir_max_level", self._reservoir_max_level),
            ("reservoir_inflow_file", self._reservoir_inflow_file),
            ("reservoir_turbine_efficiency", self._reservoir_turbine_efficiency),
            ("reservoir_evaporation_rate", self._reservoir_evaporation_rate),
            ("reservoir_pump_capacity", self._reservoir_pump_capacity),
            ("reservoir_pump_efficiency", self._reservoir_pump_efficiency),
            ("reservoir_invest_cost", self._reservoir_invest_cost),
            ("reservoir_invest_max", self._reservoir_invest_max),
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
        instances = [self._model.state.generators[i] for i in ids
                     if i in self._model.state.generators]
        if not instances:
            return

        for attr, w in self._field_map():
            if widget_is_mixed(w):
                continue
            if isinstance(w, QLineEdit):
                val = w.text()
                if attr in ("availability_file", "reservoir_inflow_file"):
                    val = val or None
            elif isinstance(w, QComboBox):
                val = w.currentText()
            else:
                val = w.value()
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

        if not widget_is_mixed(self._reservoir_spillage_allowed):
            val = self._reservoir_spillage_allowed.isChecked()
            for inst in instances:
                inst.reservoir_spillage_allowed = val

        # Sync cost curve type and data
        ct = self._fuel_curve_type.currentText()
        curve_data = self._collect_fuel_curve_data(ct)
        for inst in instances:
            inst.fuel_cost_curve_type = ct
            inst.fuel_cost_curve_data = curve_data

        if not self._multi_ids:
            inst = instances[0]
            inst.style = self._style_widget.get_style()

        if len(ids) > 3:
            self._model.stateLoaded.emit()
        else:
            for eid in ids:
                self._model.generatorUpdated.emit(eid)
                self.generatorChanged.emit(eid)

    def _on_fuel_curve_type_changed(self, idx):
        """Show/hide fuel curve stack based on selected type."""
        self._fuel_curve_stack.setCurrentIndex(idx)
        self._fuel_curve_stack.setVisible(idx > 0)
        self._on_changed()

    def _on_reservoir_toggled(self, val):
        """Show/hide reservoir fields based on capacity."""
        self._reservoir_group.setVisible(val > 0)

    def _browse_inflow_file(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, tr("generator_form.select_inflow_file"), "", "CSV (*.csv);;All (*)"
        )
        if path:
            self._reservoir_inflow_file.setText(path)
            self._on_changed()

    def eventFilter(self, obj, event):
        # Keep the trailing toggle button anchored at the right edge of
        # the QLineEdit on every resize / show.
        if obj is getattr(self, "_availability", None) and event.type() in (
            QEvent.Type.Resize, QEvent.Type.Show,
        ):
            r = obj.rect()
            btn = self._availability_btn
            x = r.right() - btn.width() - 3
            y = (r.height() - btn.height()) // 2
            btn.move(x, y)
        return super().eventFilter(obj, event)

    def _refresh_availability_action(self, text: str = ""):
        """Toggle the trailing button between '...' (empty) and '×' (set)."""
        if text and text.strip():
            self._availability_btn.setText("×")
            self._availability_btn.setToolTip(
                tr("generator_form.availability_clear_tooltip")
            )
        else:
            self._availability_btn.setText("...")
            self._availability_btn.setToolTip(
                tr("generator_form.availability_browse_tooltip")
            )

    def _on_availability_action_triggered(self):
        """Browse if the field is empty, clear if it has a path."""
        if self._availability.text().strip():
            self._availability.clear()
            self._on_changed()
        else:
            self._browse_availability_file()

    def _browse_availability_file(self):
        from PySide6.QtWidgets import QFileDialog
        start_dir = ""
        cur = self._availability.text().strip()
        if cur:
            from pathlib import Path
            p = Path(cur)
            if p.parent.exists():
                start_dir = str(p.parent)
        path, _ = QFileDialog.getOpenFileName(
            self, tr("generator_form.select_availability_file"),
            start_dir, "CSV (*.csv);;All (*)",
        )
        if path:
            self._availability.setText(path)
            self._on_changed()

    # ------------------------------------------------------------------
    # Fuel cost curve helpers
    # ------------------------------------------------------------------

    def _load_fuel_curve_data(self, data: dict, curve_type: str):
        """Populate curve widgets from stored data."""
        if curve_type == "linear":
            self._fc_price_at_zero.setValue(data.get("price_at_zero", 0.0))
            self._fc_price_at_max.setValue(data.get("price_at_max", 0.0))
            self._fc_num_segments.setValue(data.get("num_segments", 5))
        elif curve_type == "stepwise":
            blocks = data.get("blocks", [])
            self._fc_blocks_table.blockSignals(True)
            self._fc_blocks_table.setRowCount(len(blocks))
            for r, b in enumerate(blocks):
                self._fc_blocks_table.setItem(r, 0, QTableWidgetItem(str(b.get("fraction", 0.0))))
                self._fc_blocks_table.setItem(r, 1, QTableWidgetItem(str(b.get("price", 0.0))))
            self._fc_blocks_table.blockSignals(False)
        elif curve_type == "exponential":
            self._fc_base_price.setValue(data.get("base_price", 0.0))
            self._fc_scale_factor.setValue(data.get("scale_factor", 1.0))
            self._fc_exp_segments.setValue(data.get("num_segments", 5))
        # flat or unknown: nothing extra
        page_idx = {"flat": 0, "linear": 1, "stepwise": 2, "exponential": 3}.get(curve_type, 0)
        self._fuel_curve_stack.setCurrentIndex(page_idx)
        self._fuel_curve_stack.setVisible(page_idx > 0)

    def _collect_fuel_curve_data(self, curve_type: str) -> dict | None:
        """Gather curve parameters from widgets into a dict."""
        if curve_type == "flat":
            return None
        if curve_type == "linear":
            return {
                "price_at_zero": self._fc_price_at_zero.value(),
                "price_at_max": self._fc_price_at_max.value(),
                "num_segments": self._fc_num_segments.value(),
            }
        if curve_type == "stepwise":
            blocks = []
            for r in range(self._fc_blocks_table.rowCount()):
                frac_item = self._fc_blocks_table.item(r, 0)
                price_item = self._fc_blocks_table.item(r, 1)
                try:
                    frac = float(frac_item.text()) if frac_item else 0.0
                    price = float(price_item.text()) if price_item else 0.0
                except ValueError:
                    continue
                blocks.append({"fraction": frac, "price": price})
            return {"blocks": blocks}
        if curve_type == "exponential":
            return {
                "base_price": self._fc_base_price.value(),
                "scale_factor": self._fc_scale_factor.value(),
                "num_segments": self._fc_exp_segments.value(),
            }
        return None

    def _add_block_row(self, table: QTableWidget):
        """Add a row to the stepwise blocks table."""
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem("0.2"))
        table.setItem(row, 1, QTableWidgetItem("0.0"))
        self._on_changed()

    def _remove_block_row(self, table: QTableWidget):
        """Remove the last row from the stepwise blocks table."""
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
