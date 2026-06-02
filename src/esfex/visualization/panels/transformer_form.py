"""Transformer properties form."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from esfex.visualization.data.gui_model import GuiModel
from esfex.visualization.i18n import tr
from esfex.visualization.panels.visual_style_widget import VisualStyleWidget
from esfex.visualization.theme import current_theme


class TransformerForm(QWidget):
    """Property editor for a transformer."""

    transformerChanged = Signal(int)

    def __init__(self, model: GuiModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._current_idx: int | None = None
        self._multi_ids: list[str] | None = None
        self._updating = False

        layout = QFormLayout(self)

        self._system_label = QLabel("")
        layout.addRow(tr("transformer_form.system"), self._system_label)

        self._name = QLineEdit()
        self._name.editingFinished.connect(self._on_changed)
        layout.addRow(tr("transformer_form.name"), self._name)

        # Connection labels (read-only, auto-detected from lines)
        # Row labels are dynamic — updated in load_element based on resolved voltages
        self._from_row_label = QLabel(tr("transformer_form.side_a"))
        self._from_connections_label = QLabel(tr("transformer_form.no_connections"))
        self._from_connections_label.setWordWrap(True)
        layout.addRow(self._from_row_label, self._from_connections_label)

        self._to_row_label = QLabel(tr("transformer_form.side_b"))
        self._to_connections_label = QLabel(tr("transformer_form.no_connections"))
        self._to_connections_label.setWordWrap(True)
        layout.addRow(self._to_row_label, self._to_connections_label)

        # Editable bus selectors. The previous "drag the marker" pattern
        # only updated lat/lng without ever rewriting the transformer's
        # bus FKs, so the trafo stayed an intra-bus self-loop after the
        # user thought they had connected it.
        self._from_bus_combo = QComboBox()
        self._from_bus_combo.currentIndexChanged.connect(
            lambda _: self._on_bus_combo_changed("from")
        )
        layout.addRow(tr("transformer_form.from_bus"), self._from_bus_combo)
        self._to_bus_combo = QComboBox()
        self._to_bus_combo.currentIndexChanged.connect(
            lambda _: self._on_bus_combo_changed("to")
        )
        layout.addRow(tr("transformer_form.to_bus"), self._to_bus_combo)

        info_label = QLabel(tr("transformer_form.draw_lines_hint"))
        info_label.setWordWrap(True)
        info_label.setObjectName("infoLabel")
        layout.addRow(info_label)

        self._from_kv_label = QLabel(tr("transformer_form.from_voltage"))
        self._from_kv = QDoubleSpinBox()
        self._from_kv.setRange(0.1, 1000)
        self._from_kv.editingFinished.connect(self._on_changed)
        layout.addRow(self._from_kv_label, self._from_kv)

        self._to_kv_label = QLabel(tr("transformer_form.to_voltage"))
        self._to_kv = QDoubleSpinBox()
        self._to_kv.setRange(0.1, 1000)
        self._to_kv.editingFinished.connect(self._on_changed)
        layout.addRow(self._to_kv_label, self._to_kv)

        self._rated_power = QDoubleSpinBox()
        self._rated_power.setRange(0, 1e6)
        self._rated_power.editingFinished.connect(self._on_changed)
        layout.addRow(tr("transformer_form.rated_power"), self._rated_power)

        self._impedance = QDoubleSpinBox()
        self._impedance.setRange(0, 1)
        self._impedance.setDecimals(4)
        self._impedance.editingFinished.connect(self._on_changed)
        layout.addRow(tr("transformer_form.impedance"), self._impedance)

        self._losses = QDoubleSpinBox()
        self._losses.setRange(0, 1)
        self._losses.setDecimals(4)
        self._losses.editingFinished.connect(self._on_changed)
        layout.addRow(tr("transformer_form.losses"), self._losses)

        # -- Appearance --
        self._style_widget = VisualStyleWidget(
            show_color=True, show_size=True,
        )
        self._style_widget.styleChanged.connect(self._on_changed)
        layout.addRow(self._style_widget)

    def _field_map(self):
        return [
            ("name", self._name),
            ("from_voltage_kv", self._from_kv),
            ("to_voltage_kv", self._to_kv),
            ("rated_power_mva", self._rated_power),
            ("impedance_pu", self._impedance),
            ("losses_fraction", self._losses),
        ]

    def load_element(self, element_id: str):
        self._multi_ids = None
        # Strip system prefix if present (e.g. "Cuba/698" → "698")
        raw_id = element_id.rsplit("/", 1)[-1] if "/" in element_id else element_id
        try:
            idx = int(raw_id)
        except ValueError:
            return
        if idx >= len(self._model.state.transformers):
            return
        self._current_idx = idx
        trans = self._model.state.transformers[idx]

        self._system_label.setText(tr("transformer_form.system_label", name=self._model.state.name))
        self._updating = True
        self._name.setText(trans.name)

        # Query connections and resolve voltages (use raw_id without system prefix)
        connections = self._model.get_connected_elements("transformer", raw_id)
        from_kv, to_kv = self._model.resolve_transformer_side_voltages(idx)

        # Determine HV/LV assignment
        from_is_hv = True  # default: "from" is HV
        if from_kv is not None and to_kv is not None:
            from_is_hv = from_kv >= to_kv

        # Update row labels dynamically
        self._update_side_labels(from_kv, to_kv, from_is_hv)

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
            self._from_connections_label.setText(tr("transformer_form.none_draw_hint"))
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
            self._to_connections_label.setText(tr("transformer_form.none_draw_hint"))
            self._to_connections_label.setStyleSheet(f"color: {c.text_disabled}; padding: 4px;")

        # Auto-populate voltages from connected elements if available
        if from_kv is not None:
            trans.from_voltage_kv = from_kv
        if to_kv is not None:
            trans.to_voltage_kv = to_kv

        self._from_kv.setValue(trans.from_voltage_kv)
        self._to_kv.setValue(trans.to_voltage_kv)
        self._rated_power.setValue(trans.rated_power_mva)
        self._impedance.setValue(trans.impedance_pu)
        self._losses.setValue(trans.losses_fraction)

        # Refresh bus selectors with all current buses
        self._populate_bus_combo(self._from_bus_combo, trans.from_bus)
        self._populate_bus_combo(self._to_bus_combo, trans.to_bus)

        from esfex.visualization.data.default_colors import TRANSFORMER
        self._style_widget.set_default_color(TRANSFORMER)
        self._style_widget.load_style(trans.style)
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
        """User picked a different bus for one side of the transformer."""
        if self._updating or self._current_idx is None:
            return
        if self._current_idx >= len(self._model.state.transformers):
            return
        tr_obj = self._model.state.transformers[self._current_idx]
        combo = self._from_bus_combo if side == "from" else self._to_bus_combo
        new_bus_id = combo.currentData()
        if not new_bus_id:
            return
        self._model.checkpoint()
        bus = self._model.state.buses.get(new_bus_id)
        if bus is None:
            return
        if side == "from":
            tr_obj.from_bus = new_bus_id
            tr_obj.from_node = bus.parent_node
            tr_obj.from_voltage_kv = bus.voltage_kv or tr_obj.from_voltage_kv
            self._updating = True
            self._from_kv.setValue(tr_obj.from_voltage_kv)
            self._updating = False
        else:
            tr_obj.to_bus = new_bus_id
            tr_obj.to_node = bus.parent_node
            tr_obj.to_voltage_kv = bus.voltage_kv or tr_obj.to_voltage_kv
            self._updating = True
            self._to_kv.setValue(tr_obj.to_voltage_kv)
            self._updating = False
        # Notify listeners so the map / tree refresh.
        self.transformerChanged.emit(self._current_idx)

    def _update_side_labels(
        self, from_kv: float | None, to_kv: float | None, from_is_hv: bool
    ):
        """Update connection row labels and voltage field labels dynamically."""
        if from_kv is not None and to_kv is not None:
            # Both sides have connections — label HV/LV
            if from_is_hv:
                self._from_row_label.setText(
                    tr("transformer_form.hv_side", kv=f"{from_kv:.0f}")
                )
                self._to_row_label.setText(
                    tr("transformer_form.lv_side", kv=f"{to_kv:.0f}")
                )
                self._from_kv_label.setText(tr("transformer_form.hv_voltage"))
                self._to_kv_label.setText(tr("transformer_form.lv_voltage"))
            else:
                self._from_row_label.setText(
                    tr("transformer_form.lv_side", kv=f"{from_kv:.0f}")
                )
                self._to_row_label.setText(
                    tr("transformer_form.hv_side", kv=f"{to_kv:.0f}")
                )
                self._from_kv_label.setText(tr("transformer_form.lv_voltage"))
                self._to_kv_label.setText(tr("transformer_form.hv_voltage"))
        elif from_kv is not None:
            # Only "from" side has connections
            self._from_row_label.setText(
                tr("transformer_form.side_a_kv", kv=f"{from_kv:.0f}")
            )
            self._to_row_label.setText(tr("transformer_form.side_b"))
            self._from_kv_label.setText(tr("transformer_form.from_voltage"))
            self._to_kv_label.setText(tr("transformer_form.to_voltage"))
        elif to_kv is not None:
            # Only "to" side has connections
            self._from_row_label.setText(tr("transformer_form.side_a"))
            self._to_row_label.setText(
                tr("transformer_form.side_b_kv", kv=f"{to_kv:.0f}")
            )
            self._from_kv_label.setText(tr("transformer_form.from_voltage"))
            self._to_kv_label.setText(tr("transformer_form.to_voltage"))
        else:
            # No connections — generic labels
            self._from_row_label.setText(tr("transformer_form.side_a"))
            self._to_row_label.setText(tr("transformer_form.side_b"))
            self._from_kv_label.setText(tr("transformer_form.from_voltage"))
            self._to_kv_label.setText(tr("transformer_form.to_voltage"))

    def load_elements(self, element_ids: list[str]):
        from esfex.visualization.panels.multi_edit import collect_attr, set_widget_value
        trs = self._model.state.transformers
        instances = [trs[int(eid)] for eid in element_ids if int(eid) < len(trs)]
        if not instances:
            return
        self._multi_ids = element_ids
        self._current_idx = int(element_ids[0])
        self._system_label.setText(tr("transformer_form.n_selected", n=len(element_ids)))
        self._updating = True
        self._from_row_label.setText(tr("transformer_form.side_a"))
        self._to_row_label.setText(tr("transformer_form.side_b"))
        self._from_kv_label.setText(tr("transformer_form.from_voltage"))
        self._to_kv_label.setText(tr("transformer_form.to_voltage"))
        self._from_connections_label.setText(tr("transformer_form.multi_selection"))
        self._to_connections_label.setText(tr("transformer_form.multi_selection"))
        for attr, w in self._field_map():
            set_widget_value(w, collect_attr(instances, attr))
        self._updating = False

    def _on_changed(self):
        if self._updating:
            return
        self._model.checkpoint()
        from esfex.visualization.panels.multi_edit import widget_is_mixed

        if self._multi_ids:
            trs = self._model.state.transformers
            instances = [trs[int(eid)] for eid in self._multi_ids if int(eid) < len(trs)]
        elif self._current_idx is not None:
            instances = [self._model.state.transformers[self._current_idx]]
        else:
            return

        for attr, w in self._field_map():
            if widget_is_mixed(w):
                continue
            if isinstance(w, QLineEdit):
                val = w.text()
            else:
                val = w.value()
            for inst in instances:
                setattr(inst, attr, val)

        if not self._multi_ids:
            instances[0].style = self._style_widget.get_style()

        idx = self._current_idx if self._current_idx is not None else 0
        self.transformerChanged.emit(idx)
