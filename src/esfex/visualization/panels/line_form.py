"""Transmission line properties form."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import GuiModel
from esfex.visualization.i18n import tr
from esfex.visualization.panels.visual_style_widget import VisualStyleWidget


class LineForm(QWidget):
    """Property editor for a transmission line."""

    lineChanged = Signal(str)
    lineDeleteRequested = Signal(str)
    editTraceToggled = Signal(str, bool)  # line_id, enabled

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

        # --- Bus endpoints (editable) ---
        # Without this, dragging line endpoints on the map only changed
        # waypoint geometry, not the bus FKs — so manual rewiring
        # silently disappeared on save/reload.
        bus_box = QGroupBox(tr("line_form.endpoints"))
        bus_layout = QFormLayout(bus_box)
        bus_layout.setContentsMargins(6, 6, 6, 6)
        bus_layout.setSpacing(4)
        self._from_bus_combo = QComboBox()
        self._from_bus_combo.currentIndexChanged.connect(
            lambda _: self._on_bus_combo_changed("from")
        )
        bus_layout.addRow(tr("line_form.from_bus"), self._from_bus_combo)
        self._to_bus_combo = QComboBox()
        self._to_bus_combo.currentIndexChanged.connect(
            lambda _: self._on_bus_combo_changed("to")
        )
        bus_layout.addRow(tr("line_form.to_bus"), self._to_bus_combo)
        outer.addWidget(bus_box)

        # --- Properties ---
        grp = QGroupBox(tr("line_form.group_properties"))
        layout = QFormLayout(grp)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self._capacity = QDoubleSpinBox()
        self._capacity.setRange(0, 1e6)
        self._capacity.editingFinished.connect(self._on_changed)
        layout.addRow(tr("line_form.capacity"), self._capacity)

        self._voltage = QDoubleSpinBox()
        self._voltage.setRange(0, 1000)
        self._voltage.editingFinished.connect(self._on_changed)
        self._voltage.setEnabled(False)
        self._voltage.setToolTip(tr("electrical.inherited_tooltip"))
        layout.addRow(tr("line_form.voltage"), self._voltage)

        self._line_type = QComboBox()
        self._line_type.addItems(["", "overhead", "underground", "submarine"])
        self._line_type.currentTextChanged.connect(self._on_changed)
        layout.addRow(tr("line_form.type"), self._line_type)

        self._length_km = QDoubleSpinBox()
        self._length_km.setRange(0, 100000)
        self._length_km.setDecimals(2)
        self._length_km.setSpecialValueText("Auto")
        self._length_km.setReadOnly(True)
        layout.addRow(tr("line_form.length"), self._length_km)

        self._base_impedance = QDoubleSpinBox()
        self._base_impedance.setRange(0, 1e6)
        self._base_impedance.setDecimals(2)
        self._base_impedance.setSpecialValueText("Default")
        self._base_impedance.editingFinished.connect(self._on_changed)
        layout.addRow(tr("line_form.base_impedance"), self._base_impedance)

        self._reactance_per_km = QDoubleSpinBox()
        self._reactance_per_km.setRange(0, 100)
        self._reactance_per_km.setDecimals(4)
        self._reactance_per_km.setSpecialValueText("Default")
        self._reactance_per_km.editingFinished.connect(self._on_changed)
        layout.addRow(tr("line_form.reactance_per_km"), self._reactance_per_km)

        self._reactance_pu = QDoubleSpinBox()
        self._reactance_pu.setRange(0, 100)
        self._reactance_pu.setDecimals(6)
        self._reactance_pu.setSpecialValueText("Global")
        self._reactance_pu.editingFinished.connect(self._on_changed)
        layout.addRow(tr("line_form.reactance_pu"), self._reactance_pu)

        self._resistance_pu = QDoubleSpinBox()
        self._resistance_pu.setRange(0, 100)
        self._resistance_pu.setDecimals(6)
        self._resistance_pu.setSpecialValueText("Global")
        self._resistance_pu.editingFinished.connect(self._on_changed)
        layout.addRow(tr("line_form.resistance_pu"), self._resistance_pu)

        self._susceptance_pu = QDoubleSpinBox()
        self._susceptance_pu.setRange(0, 100)
        self._susceptance_pu.setDecimals(6)
        self._susceptance_pu.setSpecialValueText("Global")
        self._susceptance_pu.editingFinished.connect(self._on_changed)
        layout.addRow(tr("line_form.susceptance_pu"), self._susceptance_pu)

        self._num_circuits = QSpinBox()
        self._num_circuits.setRange(1, 10)
        self._num_circuits.valueChanged.connect(self._on_changed)
        layout.addRow(tr("line_form.circuits"), self._num_circuits)

        self._frequency_hz = QDoubleSpinBox()
        self._frequency_hz.setRange(0.0, 400)
        self._frequency_hz.setDecimals(1)
        self._frequency_hz.setSpecialValueText("N/A")
        self._frequency_hz.editingFinished.connect(self._on_changed)
        self._frequency_hz.setEnabled(False)
        self._frequency_hz.setToolTip(tr("electrical.inherited_tooltip"))
        layout.addRow(tr("line_form.frequency"), self._frequency_hz)

        self._current_type = QComboBox()
        self._current_type.addItems(["AC", "DC"])
        self._current_type.currentTextChanged.connect(self._on_current_type_changed)
        self._current_type.setEnabled(False)
        self._current_type.setToolTip(tr("electrical.inherited_tooltip"))
        layout.addRow(tr("line_form.current_type"), self._current_type)

        outer.addWidget(grp)

        # -- Appearance --
        self._style_widget = VisualStyleWidget(
            show_color=True, show_width=True, show_size=False,
        )
        self._style_widget.styleChanged.connect(self._on_changed)
        outer.addWidget(self._style_widget)

        # -- Edit / Delete buttons --
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(4, 2, 4, 2)

        self._edit_trace_btn = QPushButton(tr("line_form.edit_trace"))
        self._edit_trace_btn.setCheckable(True)
        self._edit_trace_btn.setObjectName("editTraceButton")
        self._edit_trace_btn.toggled.connect(self._on_edit_trace_toggled)
        btn_row.addWidget(self._edit_trace_btn)

        self._delete_btn = QPushButton(tr("line_form.delete_line"))
        self._delete_btn.setObjectName("deleteButton")
        self._delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self._delete_btn)

        outer.addLayout(btn_row)
        outer.addStretch()

    def _get_lines(self, ids: list[str]):
        """Get line instances by line_id."""
        id_set = set(ids)
        return [ln for ln in self._model.state.transmission_lines if ln.line_id in id_set]

    def load_element(self, element_id: str):
        self._multi_ids = None
        self._current_id = element_id
        line = None
        for ln in self._model.state.transmission_lines:
            if ln.line_id == element_id:
                line = ln
                break
        if not line:
            return

        self._updating = True

        # Propagate electrical properties from endpoint buses
        self._model.propagate_bus_to_element("transmission_line", element_id)

        # Header: system + node endpoints
        from_name = ""
        to_name = ""
        for n in self._model.state.nodes:
            if n.index == line.from_node:
                from_name = n.name
            if n.index == line.to_node:
                to_name = n.name
        self._header_label.setText(
            f"{self._model.state.name}  |  {from_name} \u2192 {to_name}"
        )

        self._capacity.setValue(line.capacity_mw)
        self._voltage.setValue(line.voltage_kv or 0)

        lt_idx = self._line_type.findText(line.line_type or "")
        if lt_idx >= 0:
            self._line_type.setCurrentIndex(lt_idx)

        self._length_km.setValue(line.length_km or 0)
        self._base_impedance.setValue(line.base_impedance or 0)
        self._reactance_per_km.setValue(line.reactance_per_km or 0)
        self._reactance_pu.setValue(line.reactance_pu or 0)
        self._resistance_pu.setValue(line.resistance_pu or 0)
        self._susceptance_pu.setValue(line.susceptance_pu or 0)
        self._num_circuits.setValue(line.num_circuits)

        # Load current type first
        current_type = getattr(line, 'current_type', 'AC')
        ct_idx = self._current_type.findText(current_type)
        if ct_idx >= 0:
            self._current_type.setCurrentIndex(ct_idx)

        # Load frequency with proper defaults based on current type
        frequency = getattr(line, 'frequency_hz', 50.0)
        if current_type == "AC" and (frequency == 0.0 or frequency is None):
            frequency = 50.0  # Default for AC lines
        elif current_type == "DC":
            frequency = 0.0  # DC has no frequency
        self._frequency_hz.setValue(frequency)

        from esfex.visualization.data.default_colors import TRANSMISSION_LINE
        self._style_widget.set_default_color(TRANSMISSION_LINE)
        self._style_widget.load_style(line.style)

        # Refresh bus selectors with current state
        self._populate_bus_combo(self._from_bus_combo, line.from_bus)
        self._populate_bus_combo(self._to_bus_combo, line.to_bus)

        # Reset edit state
        self._edit_trace_btn.setChecked(False)
        self._editing_trace = False

        self._updating = False

    def _populate_bus_combo(self, combo: QComboBox, current_bus_id: str):
        """Populate a bus selector with every bus in the current state."""
        combo.blockSignals(True)
        combo.clear()
        selected_idx = 0
        for i, (bid, bus) in enumerate(self._model.state.buses.items()):
            label = f"{bid}  ({bus.voltage_kv:g} kV)"
            combo.addItem(label, bid)
            if bid == current_bus_id:
                selected_idx = i
        combo.setCurrentIndex(selected_idx)
        combo.blockSignals(False)

    def _on_bus_combo_changed(self, side: str):
        """User picked a different bus for one end of the line."""
        if self._updating or self._current_id is None:
            return
        line = next(
            (ln for ln in self._model.state.transmission_lines
             if ln.line_id == self._current_id),
            None,
        )
        if line is None:
            return
        combo = self._from_bus_combo if side == "from" else self._to_bus_combo
        new_bus_id = combo.currentData()
        if not new_bus_id:
            return
        bus = self._model.state.buses.get(new_bus_id)
        if bus is None:
            return
        self._model.checkpoint()
        from esfex.visualization.data.gui_model import EndpointRef
        if side == "from":
            line.from_bus = new_bus_id
            line.from_node = bus.parent_node
            line.from_endpoint = EndpointRef("bus", new_bus_id)
        else:
            line.to_bus = new_bus_id
            line.to_node = bus.parent_node
            line.to_endpoint = EndpointRef("bus", new_bus_id)
        self.lineChanged.emit(self._current_id)

    def stop_editing(self):
        """Disable trace editing (called on deselection)."""
        if self._editing_trace and self._current_id:
            self._edit_trace_btn.setChecked(False)

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
            tr("line_form.confirm_delete_title"),
            tr("line_form.confirm_delete_msg", line_id=self._current_id),
        ):
            if self._editing_trace:
                self._edit_trace_btn.setChecked(False)
            self.lineDeleteRequested.emit(self._current_id)

    def load_elements(self, element_ids: list[str]):
        """Load multiple lines for batch editing."""
        from esfex.visualization.panels.multi_edit import collect_attr, set_widget_value

        instances = self._get_lines(element_ids)
        if not instances:
            return
        self._multi_ids = element_ids
        self._current_id = element_ids[0]
        self._header_label.setText(tr("line_form.n_selected", n=len(element_ids)))
        self._updating = True

        for attr, w in self._field_map():
            set_widget_value(w, collect_attr(instances, attr))

        self._edit_trace_btn.setChecked(False)
        self._edit_trace_btn.setEnabled(False)
        self._editing_trace = False
        self._updating = False

    def _field_map(self):
        return [
            ("capacity_mw", self._capacity),
            ("line_type", self._line_type),
            ("base_impedance", self._base_impedance),
            ("reactance_per_km", self._reactance_per_km),
            ("reactance_pu", self._reactance_pu),
            ("resistance_pu", self._resistance_pu),
            ("susceptance_pu", self._susceptance_pu),
            ("num_circuits", self._num_circuits),
        ]

    def _on_current_type_changed(self, current_type: str):
        """Auto-adjust frequency when current type changes."""
        if self._updating:
            return

        # Auto-set frequency based on current type
        if current_type == "AC":
            # If switching to AC and frequency is 0, set to 50 Hz
            if self._frequency_hz.value() == 0.0:
                self._frequency_hz.setValue(50.0)
        elif current_type == "DC":
            # DC lines have no frequency
            self._frequency_hz.setValue(0.0)

        # Propagate changes
        self._on_changed()

    def _on_changed(self):
        if self._updating:
            return
        self._model.checkpoint()
        from esfex.visualization.panels.multi_edit import widget_is_mixed

        ids = self._multi_ids or ([self._current_id] if self._current_id else [])
        lines = self._get_lines(ids)
        if not lines:
            return

        # Build kwargs from non-mixed fields
        kwargs: dict = {}
        field_to_kwarg = {
            "capacity_mw": "capacity_mw",
            "line_type": "line_type",
            "base_impedance": "base_impedance",
            "reactance_per_km": "reactance_per_km",
            "reactance_pu": "reactance_pu",
            "resistance_pu": "resistance_pu",
            "susceptance_pu": "susceptance_pu",
            "num_circuits": "num_circuits",
        }
        for attr, w in self._field_map():
            if widget_is_mixed(w):
                continue
            if isinstance(w, QComboBox):
                val = w.currentText()
                if attr == "line_type":
                    val = val or None
            else:
                val = w.value()
                if attr in ("voltage_kv", "base_impedance", "reactance_per_km",
                            "reactance_pu", "resistance_pu", "susceptance_pu",
                            "length_km"):
                    val = val or None
            kwargs[field_to_kwarg[attr]] = val

        if not self._multi_ids:
            for ln in lines:
                ln.style = self._style_widget.get_style()

        for lid in ids:
            self._model.update_line(lid, **kwargs)
            self.lineChanged.emit(lid)

    def set_length_km(self, km: float):
        """Update the displayed length (called by main_window on trace changes)."""
        self._updating = True
        self._length_km.setValue(km)
        self._updating = False
