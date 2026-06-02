"""AC/DC converter properties form."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import GuiModel
from esfex.visualization.i18n import tr
from esfex.visualization.panels.visual_style_widget import VisualStyleWidget
from esfex.visualization.theme import current_theme


class ACDCConverterForm(QWidget):
    """Property editor for an AC/DC converter (rectifier/inverter)."""

    converterChanged = Signal(int)

    def __init__(self, model: GuiModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._current_idx: int | None = None
        self._multi_ids: list[str] | None = None
        self._updating = False

        outer = QVBoxLayout(self)

        # ── Identity ──
        id_group = QGroupBox(tr("acdc_form.group_identity"))
        id_layout = QFormLayout(id_group)

        self._system_label = QLabel("")
        id_layout.addRow(tr("acdc_form.system_label"), self._system_label)

        self._name = QLineEdit()
        self._name.editingFinished.connect(self._on_changed)
        id_layout.addRow(tr("acdc_form.name_label"), self._name)

        self._converter_type = QComboBox()
        self._converter_type.addItems(["VSC", "LCC"])
        self._converter_type.currentIndexChanged.connect(self._on_changed)
        id_layout.addRow(tr("acdc_form.type_label"), self._converter_type)

        outer.addWidget(id_group)

        # ── Topology ──
        topo_group = QGroupBox(tr("acdc_form.group_topology"))
        topo_layout = QFormLayout(topo_group)

        # Connection labels (read-only, auto-detected from lines)
        self._from_connections_label = QLabel(tr("acdc_form.no_connections"))
        self._from_connections_label.setWordWrap(True)
        topo_layout.addRow(tr("acdc_form.connected_from_ac"), self._from_connections_label)

        self._to_connections_label = QLabel(tr("acdc_form.no_connections"))
        self._to_connections_label.setWordWrap(True)
        topo_layout.addRow(tr("acdc_form.connected_to_dc"), self._to_connections_label)

        # Editable bus selectors — see transformer_form.py for the
        # rationale (drag-only rewiring silently disappeared on save).
        self._from_bus_combo = QComboBox()
        self._from_bus_combo.currentIndexChanged.connect(
            lambda _: self._on_bus_combo_changed("from")
        )
        topo_layout.addRow(tr("acdc_form.from_bus"), self._from_bus_combo)
        self._to_bus_combo = QComboBox()
        self._to_bus_combo.currentIndexChanged.connect(
            lambda _: self._on_bus_combo_changed("to")
        )
        topo_layout.addRow(tr("acdc_form.to_bus"), self._to_bus_combo)

        info_label = QLabel(tr("acdc_form.draw_lines_hint"))
        info_label.setWordWrap(True)
        info_label.setObjectName("infoLabel")
        topo_layout.addRow(info_label)

        self._from_kv = QDoubleSpinBox()
        self._from_kv.setRange(0.1, 2000)
        self._from_kv.editingFinished.connect(self._on_changed)
        self._from_kv.setEnabled(False)
        self._from_kv.setToolTip(tr("electrical.inherited_tooltip"))
        topo_layout.addRow(tr("acdc_form.ac_voltage"), self._from_kv)

        self._dc_kv = QDoubleSpinBox()
        self._dc_kv.setRange(0.1, 2000)
        self._dc_kv.editingFinished.connect(self._on_changed)
        self._dc_kv.setEnabled(False)
        self._dc_kv.setToolTip(tr("electrical.inherited_tooltip"))
        topo_layout.addRow(tr("acdc_form.dc_voltage"), self._dc_kv)

        outer.addWidget(topo_group)

        # ── Capacity ──
        cap_group = QGroupBox(tr("acdc_form.group_capacity"))
        cap_layout = QFormLayout(cap_group)

        self._rated_power = QDoubleSpinBox()
        self._rated_power.setRange(0, 1e6)
        self._rated_power.editingFinished.connect(self._on_changed)
        cap_layout.addRow(tr("acdc_form.rated_power"), self._rated_power)

        self._min_power = QDoubleSpinBox()
        self._min_power.setRange(0, 1e6)
        self._min_power.editingFinished.connect(self._on_changed)
        cap_layout.addRow(tr("acdc_form.min_power"), self._min_power)

        outer.addWidget(cap_group)

        # ── Efficiency ──
        eff_group = QGroupBox(tr("acdc_form.group_efficiency"))
        eff_layout = QFormLayout(eff_group)

        self._eff_rectify = QDoubleSpinBox()
        self._eff_rectify.setRange(0, 1)
        self._eff_rectify.setDecimals(4)
        self._eff_rectify.setSingleStep(0.01)
        self._eff_rectify.editingFinished.connect(self._on_changed)
        eff_layout.addRow(tr("acdc_form.rectify_eff"), self._eff_rectify)

        self._eff_invert = QDoubleSpinBox()
        self._eff_invert.setRange(0, 1)
        self._eff_invert.setDecimals(4)
        self._eff_invert.setSingleStep(0.01)
        self._eff_invert.editingFinished.connect(self._on_changed)
        eff_layout.addRow(tr("acdc_form.invert_eff"), self._eff_invert)

        self._standby_losses = QDoubleSpinBox()
        self._standby_losses.setRange(0, 1e4)
        self._standby_losses.editingFinished.connect(self._on_changed)
        eff_layout.addRow(tr("acdc_form.standby_losses"), self._standby_losses)

        outer.addWidget(eff_group)

        # ── Reactive Power ──
        q_group = QGroupBox(tr("acdc_form.group_reactive"))
        q_layout = QFormLayout(q_group)

        self._q_min = QDoubleSpinBox()
        self._q_min.setRange(-1e6, 0)
        self._q_min.editingFinished.connect(self._on_changed)
        q_layout.addRow(tr("acdc_form.q_min"), self._q_min)

        self._q_max = QDoubleSpinBox()
        self._q_max.setRange(0, 1e6)
        self._q_max.editingFinished.connect(self._on_changed)
        q_layout.addRow(tr("acdc_form.q_max"), self._q_max)

        self._pf = QDoubleSpinBox()
        self._pf.setRange(0, 1)
        self._pf.setDecimals(3)
        self._pf.editingFinished.connect(self._on_changed)
        q_layout.addRow(tr("acdc_form.power_factor"), self._pf)

        outer.addWidget(q_group)

        # ── Impedance ──
        z_group = QGroupBox(tr("acdc_form.group_impedance"))
        z_layout = QFormLayout(z_group)

        self._impedance = QDoubleSpinBox()
        self._impedance.setRange(0, 1)
        self._impedance.setDecimals(4)
        self._impedance.editingFinished.connect(self._on_changed)
        z_layout.addRow(tr("acdc_form.impedance"), self._impedance)

        self._resistance = QDoubleSpinBox()
        self._resistance.setRange(0, 1)
        self._resistance.setDecimals(4)
        self._resistance.editingFinished.connect(self._on_changed)
        z_layout.addRow(tr("acdc_form.resistance"), self._resistance)

        outer.addWidget(z_group)

        # ── Economics ──
        econ_group = QGroupBox(tr("acdc_form.group_economics"))
        econ_layout = QFormLayout(econ_group)

        self._fixed_cost = QDoubleSpinBox()
        self._fixed_cost.setRange(0, 1e9)
        self._fixed_cost.editingFinished.connect(self._on_changed)
        econ_layout.addRow(tr("acdc_form.fixed_cost"), self._fixed_cost)

        self._variable_cost = QDoubleSpinBox()
        self._variable_cost.setRange(0, 1e6)
        self._variable_cost.editingFinished.connect(self._on_changed)
        econ_layout.addRow(tr("acdc_form.variable_cost"), self._variable_cost)

        outer.addWidget(econ_group)

        # ── Lifecycle ──
        life_group = QGroupBox(tr("acdc_form.group_lifecycle"))
        life_layout = QFormLayout(life_group)

        self._lifetime = QSpinBox()
        self._lifetime.setRange(1, 100)
        self._lifetime.editingFinished.connect(self._on_changed)
        life_layout.addRow(tr("acdc_form.lifetime"), self._lifetime)

        self._initial_age = QSpinBox()
        self._initial_age.setRange(0, 100)
        self._initial_age.editingFinished.connect(self._on_changed)
        life_layout.addRow(tr("acdc_form.initial_age"), self._initial_age)

        self._degradation = QDoubleSpinBox()
        self._degradation.setRange(0, 0.1)
        self._degradation.setDecimals(4)
        self._degradation.setSingleStep(0.001)
        self._degradation.editingFinished.connect(self._on_changed)
        life_layout.addRow(tr("acdc_form.degradation_rate"), self._degradation)

        outer.addWidget(life_group)

        # ── Appearance ──
        self._style_widget = VisualStyleWidget(show_color=True, show_size=True)
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
        if idx >= len(self._model.state.acdc_converters):
            return
        self._multi_ids = None
        self._current_idx = idx
        conv = self._model.state.acdc_converters[idx]

        self._system_label.setText(self._model.state.name)
        self._updating = True

        # Propagate voltage from connected buses
        self._model.propagate_bus_to_element("acdc_converter", raw_id)

        self._name.setText(conv.name)
        ct_idx = self._converter_type.findText(conv.converter_type)
        if ct_idx >= 0:
            self._converter_type.setCurrentIndex(ct_idx)

        # Query and display connections
        connections = self._model.get_connected_elements("acdc_converter", raw_id)

        c = current_theme().colors
        if connections["from"]:
            from_strs = [
                self._model.format_connected_element(et, eid)
                for et, eid, _ in connections["from"]
            ]
            self._from_connections_label.setText("\n".join(from_strs))
            self._from_connections_label.setStyleSheet(
                f"color: {c.accent_secondary}; padding: 4px;"
            )
        else:
            self._from_connections_label.setText(tr("acdc_form.none_draw_lines"))
            self._from_connections_label.setStyleSheet(
                f"color: {c.text_disabled}; padding: 4px;"
            )

        if connections["to"]:
            to_strs = [
                self._model.format_connected_element(et, eid)
                for et, eid, _ in connections["to"]
            ]
            self._to_connections_label.setText("\n".join(to_strs))
            self._to_connections_label.setStyleSheet(f"color: {c.accent_secondary}; padding: 4px;")
        else:
            self._to_connections_label.setText(tr("acdc_form.none_draw_lines"))
            self._to_connections_label.setStyleSheet(f"color: {c.text_disabled}; padding: 4px;")

        self._from_kv.setValue(conv.from_voltage_kv)
        self._dc_kv.setValue(conv.dc_voltage_kv)
        self._rated_power.setValue(conv.rated_power_mva)
        self._min_power.setValue(conv.min_power_mva)
        self._eff_rectify.setValue(conv.efficiency_rectify)
        self._eff_invert.setValue(conv.efficiency_invert)
        self._standby_losses.setValue(conv.standby_losses_mw)
        self._q_min.setValue(conv.reactive_power_min_mvar)
        self._q_max.setValue(conv.reactive_power_max_mvar)
        self._pf.setValue(conv.power_factor)
        self._impedance.setValue(conv.impedance_pu)
        self._resistance.setValue(conv.resistance_pu)
        self._fixed_cost.setValue(conv.fixed_cost)
        self._variable_cost.setValue(conv.variable_cost)
        self._lifetime.setValue(conv.life_time)
        self._initial_age.setValue(conv.initial_age)
        self._degradation.setValue(conv.degradation_rate)
        from esfex.visualization.data.default_colors import ACDC_CONVERTER
        self._style_widget.set_default_color(ACDC_CONVERTER)
        self._style_widget.load_style(conv.style)

        self._populate_bus_combo(self._from_bus_combo, conv.from_bus)
        self._populate_bus_combo(self._to_bus_combo, conv.to_bus)

        self._updating = False

    def _populate_bus_combo(self, combo: QComboBox, current_bus_id: str):
        combo.blockSignals(True)
        combo.clear()
        selected_idx = 0
        for i, (bid, bus) in enumerate(self._model.state.buses.items()):
            label = f"{bid}  ({bus.voltage_kv:g} kV, {bus.current_type})"
            combo.addItem(label, bid)
            if bid == current_bus_id:
                selected_idx = i
        combo.setCurrentIndex(selected_idx)
        combo.blockSignals(False)

    def _on_bus_combo_changed(self, side: str):
        if self._updating or self._current_idx is None:
            return
        if self._current_idx >= len(self._model.state.acdc_converters):
            return
        conv = self._model.state.acdc_converters[self._current_idx]
        combo = self._from_bus_combo if side == "from" else self._to_bus_combo
        new_bus_id = combo.currentData()
        if not new_bus_id:
            return
        bus = self._model.state.buses.get(new_bus_id)
        if bus is None:
            return
        self._model.checkpoint()
        if side == "from":
            conv.from_bus = new_bus_id
            conv.from_node = bus.parent_node
            conv.from_voltage_kv = bus.voltage_kv or conv.from_voltage_kv
            self._updating = True
            self._from_kv.setValue(conv.from_voltage_kv)
            self._updating = False
        else:
            conv.to_bus = new_bus_id
            conv.to_node = bus.parent_node
            conv.dc_voltage_kv = bus.voltage_kv or conv.dc_voltage_kv
            self._updating = True
            self._dc_kv.setValue(conv.dc_voltage_kv)
            self._updating = False
        self.converterChanged.emit(self._current_idx)

    def _field_map(self):
        return [
            ("name", self._name),
            ("converter_type", self._converter_type),
            ("rated_power_mva", self._rated_power),
            ("min_power_mva", self._min_power),
            ("efficiency_rectify", self._eff_rectify),
            ("efficiency_invert", self._eff_invert),
            ("standby_losses_mw", self._standby_losses),
            ("reactive_power_min_mvar", self._q_min),
            ("reactive_power_max_mvar", self._q_max),
            ("power_factor", self._pf),
            ("impedance_pu", self._impedance),
            ("resistance_pu", self._resistance),
            ("fixed_cost", self._fixed_cost),
            ("variable_cost", self._variable_cost),
            ("life_time", self._lifetime),
            ("initial_age", self._initial_age),
            ("degradation_rate", self._degradation),
        ]

    def load_elements(self, element_ids: list[str]):
        from esfex.visualization.panels.multi_edit import collect_attr, set_widget_value
        convs = self._model.state.acdc_converters
        raw_ids = [eid.rsplit("/", 1)[-1] if "/" in eid else eid for eid in element_ids]
        instances = [convs[int(rid)] for rid in raw_ids if int(rid) < len(convs)]
        if not instances:
            return
        self._multi_ids = element_ids
        self._current_idx = int(raw_ids[0])
        self._system_label.setText(tr("acdc_form.multi_selected", count=len(element_ids)))
        self._updating = True
        # Note: Connections are auto-detected from lines, not manually set
        self._from_connections_label.setText(tr("acdc_form.multi_view_individually"))
        self._to_connections_label.setText(tr("acdc_form.multi_view_individually"))
        for attr, w in self._field_map():
            set_widget_value(w, collect_attr(instances, attr))
        self._updating = False

    def _on_changed(self):
        if self._updating:
            return
        self._model.checkpoint()
        from esfex.visualization.panels.multi_edit import widget_is_mixed

        if self._multi_ids:
            convs = self._model.state.acdc_converters
            instances = [convs[int(eid)] for eid in self._multi_ids if int(eid) < len(convs)]
        elif self._current_idx is not None:
            instances = [self._model.state.acdc_converters[self._current_idx]]
        else:
            return

        for attr, w in self._field_map():
            if widget_is_mixed(w):
                continue
            if isinstance(w, QLineEdit):
                val = w.text()
            elif isinstance(w, QComboBox):
                val = w.currentText()
            else:
                val = w.value()
            for inst in instances:
                setattr(inst, attr, val)

        if not self._multi_ids:
            instances[0].style = self._style_widget.get_style()

        idx = self._current_idx if self._current_idx is not None else 0
        self.converterChanged.emit(idx)
