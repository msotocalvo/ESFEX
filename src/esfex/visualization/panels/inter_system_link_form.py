"""Properties form for inter-system links (meta_network.systems_links).

Mirrors :class:`LineForm` so the editor exposes the same fields a user
sees for an intra-system transmission line, adapted to the cross-system
context: endpoint buses come from two different ``GuiSystemState``
instances (the ``from_system`` and ``to_system`` keys of
``_all_states``), and the link itself lives outside any single
state in ``GuiModel.inter_system_links``.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import EndpointRef, GuiModel
from esfex.visualization.i18n import tr
from esfex.visualization.panels.visual_style_widget import VisualStyleWidget


class InterSystemLinkForm(QWidget):
    """Property editor for a single inter-system link (full LineForm parity)."""

    linkChanged = Signal(str)            # link_id
    linkDeleteRequested = Signal(str)    # link_id
    editTraceToggled = Signal(str, bool)  # link_id, enabled

    def __init__(self, model: GuiModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._current_id: str | None = None
        self._updating = False
        self._editing_trace = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # --- Header: cross-system endpoints (read-only) ---
        self._header_label = QLabel("")
        self._header_label.setObjectName("headerLabel")
        self._header_label.setWordWrap(True)
        outer.addWidget(self._header_label)

        # --- Bus endpoints (editable combos populated from each system) ---
        bus_box = QGroupBox(tr("line_form.endpoints"))
        bus_layout = QFormLayout(bus_box)
        bus_layout.setContentsMargins(6, 6, 6, 6)
        bus_layout.setSpacing(4)
        self._from_bus_combo = QComboBox()
        self._from_bus_combo.currentIndexChanged.connect(
            lambda _: self._on_bus_combo_changed("from"))
        bus_layout.addRow(tr("line_form.from_bus"), self._from_bus_combo)
        self._to_bus_combo = QComboBox()
        self._to_bus_combo.currentIndexChanged.connect(
            lambda _: self._on_bus_combo_changed("to"))
        bus_layout.addRow(tr("line_form.to_bus"), self._to_bus_combo)
        outer.addWidget(bus_box)

        # --- Electrical / capacity properties ---
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

        # Base impedance / reactance per km / susceptance: editable
        # numeric inputs. No specialValueText (the user can read the
        # current value at a glance and override it) and 6-decimal
        # precision to match electrical units in the YAML.
        self._base_impedance = QDoubleSpinBox()
        self._base_impedance.setRange(0, 1e6)
        self._base_impedance.setDecimals(2)
        self._base_impedance.editingFinished.connect(self._on_changed)
        layout.addRow(tr("line_form.base_impedance"), self._base_impedance)

        self._reactance_per_km = QDoubleSpinBox()
        self._reactance_per_km.setRange(0, 100)
        self._reactance_per_km.setDecimals(4)
        self._reactance_per_km.editingFinished.connect(self._on_changed)
        layout.addRow(tr("line_form.reactance_per_km"), self._reactance_per_km)

        self._reactance_pu = QDoubleSpinBox()
        self._reactance_pu.setRange(0, 100)
        self._reactance_pu.setDecimals(6)
        self._reactance_pu.editingFinished.connect(self._on_changed)
        layout.addRow(tr("line_form.reactance_pu"), self._reactance_pu)

        self._resistance_pu = QDoubleSpinBox()
        self._resistance_pu.setRange(0, 100)
        self._resistance_pu.setDecimals(6)
        self._resistance_pu.editingFinished.connect(self._on_changed)
        layout.addRow(tr("line_form.resistance_pu"), self._resistance_pu)

        self._susceptance_pu = QDoubleSpinBox()
        self._susceptance_pu.setRange(0, 100)
        self._susceptance_pu.setDecimals(6)
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
        self._current_type.currentTextChanged.connect(self._on_changed)
        self._current_type.setEnabled(False)
        self._current_type.setToolTip(tr("electrical.inherited_tooltip"))
        layout.addRow(tr("line_form.current_type"), self._current_type)

        outer.addWidget(grp)

        # --- Economic properties ---
        econ = QGroupBox("Economic")
        econ_layout = QFormLayout(econ)
        econ_layout.setContentsMargins(6, 6, 6, 6)
        econ_layout.setSpacing(4)

        self._investment_cost = QDoubleSpinBox()
        self._investment_cost.setRange(0, 1e12)
        self._investment_cost.setDecimals(2)
        self._investment_cost.setSuffix(" $/MW")
        self._investment_cost.editingFinished.connect(self._on_changed)
        econ_layout.addRow("Investment cost", self._investment_cost)

        self._max_investment = QDoubleSpinBox()
        self._max_investment.setRange(0, 1e6)
        self._max_investment.setDecimals(2)
        self._max_investment.setSuffix(" MW")
        self._max_investment.editingFinished.connect(self._on_changed)
        econ_layout.addRow("Max investment", self._max_investment)

        self._cost_per_mw_km = QDoubleSpinBox()
        self._cost_per_mw_km.setRange(0, 1e6)
        self._cost_per_mw_km.setDecimals(4)
        self._cost_per_mw_km.setSuffix(" $/MW·km")
        self._cost_per_mw_km.editingFinished.connect(self._on_changed)
        econ_layout.addRow("Operational cost", self._cost_per_mw_km)

        self._loss_factor = QDoubleSpinBox()
        self._loss_factor.setRange(0, 1)
        self._loss_factor.setDecimals(4)
        self._loss_factor.setSingleStep(0.005)
        self._loss_factor.editingFinished.connect(self._on_changed)
        econ_layout.addRow("Loss factor", self._loss_factor)

        outer.addWidget(econ)

        # --- Appearance ---
        self._style_widget = VisualStyleWidget(
            show_color=True, show_width=True, show_size=False,
        )
        self._style_widget.styleChanged.connect(self._on_style_changed)
        outer.addWidget(self._style_widget)

        # --- Edit / Delete buttons ---
        # Delete on the LEFT in destructive red (objectName picks up
        # the deleteButton QSS rule from theme.py).
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(4, 2, 4, 2)

        self._delete_btn = QPushButton(tr("line_form.delete_line"))
        self._delete_btn.setObjectName("deleteButton")
        self._delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self._delete_btn)

        self._edit_trace_btn = QPushButton(tr("line_form.edit_trace"))
        self._edit_trace_btn.setCheckable(True)
        self._edit_trace_btn.setObjectName("editTraceButton")
        self._edit_trace_btn.toggled.connect(self._on_edit_trace_toggled)
        btn_row.addWidget(self._edit_trace_btn)

        btn_row.addStretch(1)
        outer.addLayout(btn_row)
        outer.addStretch(1)

    # ------------------------------------------------------------------
    def _find(self, link_id: str):
        for lk in self._model.inter_system_links:
            if lk.link_id == link_id:
                return lk
        return None

    def load_element(self, link_id: str) -> None:
        """PropertiesPanel contract; populate from the model's link."""
        self._current_id = link_id
        link = self._find(link_id)
        if link is None:
            return

        # Pull live voltage / frequency / current_type from the endpoint
        # buses AND recompute polyline length from current geometry.
        # Both routines only mutate the dataclass — neither calls back
        # into the form (so no recursion).
        win = self.window()
        if win is not None:
            if hasattr(win, "_propagate_islink_bus_properties"):
                win._propagate_islink_bus_properties(link_id)
            if hasattr(win, "_auto_update_islink_length"):
                win._auto_update_islink_length(link_id)
            link = self._find(link_id) or link

        self._updating = True
        try:
            self._refresh_header(link)
            self._capacity.setValue(float(link.capacity_mw or 0.0))
            self._voltage.setValue(float(link.voltage_kv or 0.0))
            lt_idx = self._line_type.findText(link.line_type or "")
            if lt_idx >= 0:
                self._line_type.setCurrentIndex(lt_idx)
            # length_km is shown read-only; if absent, fall back to
            # distance_km — they mean the same thing on a link.
            self._length_km.setValue(
                float(link.length_km if link.length_km is not None
                      else (link.distance_km or 0.0))
            )
            self._base_impedance.setValue(float(link.base_impedance or 0.0))
            self._reactance_per_km.setValue(float(link.reactance_per_km or 0.0))
            self._reactance_pu.setValue(float(link.reactance_pu or 0.0))
            self._resistance_pu.setValue(float(link.resistance_pu or 0.0))
            self._susceptance_pu.setValue(float(link.susceptance_pu or 0.0))
            self._num_circuits.setValue(int(link.num_circuits or 1))
            ct = getattr(link, "current_type", "AC") or "AC"
            ct_idx = self._current_type.findText(ct)
            if ct_idx >= 0:
                self._current_type.setCurrentIndex(ct_idx)
            freq = getattr(link, "frequency_hz", 50.0) or 50.0
            if ct == "DC":
                freq = 0.0
            self._frequency_hz.setValue(float(freq))

            # Economic
            self._investment_cost.setValue(float(link.investment_cost or 0.0))
            self._max_investment.setValue(float(link.max_investment_mw or 0.0))
            self._cost_per_mw_km.setValue(float(link.cost_per_mw_km or 0.0))
            self._loss_factor.setValue(float(link.loss_factor or 0.0))

            # Style
            try:
                from esfex.visualization.data.default_colors import TRANSMISSION_LINE
                self._style_widget.set_default_color(TRANSMISSION_LINE)
            except Exception:
                pass
            self._style_widget.load_style(link.style)

            # Bus selectors — each combo lists the buses of its owner system.
            self._populate_bus_combo(self._from_bus_combo, link.from_system,
                                      link.from_endpoint)
            self._populate_bus_combo(self._to_bus_combo, link.to_system,
                                      link.to_endpoint)

            self._edit_trace_btn.setChecked(False)
            self._editing_trace = False
        finally:
            self._updating = False

    def _refresh_header(self, link) -> None:
        arrow = "→" if link.link_type == "transmission" else "⇢"
        # Resolve node names from each system's state for clarity.
        from_name = self._node_name(link.from_system, link.from_node)
        to_name   = self._node_name(link.to_system,   link.to_node)
        self._header_label.setText(
            f"<b>{link.link_id}</b> ({link.link_type})<br>"
            f"{link.from_system}: {from_name}  {arrow}  {link.to_system}: {to_name}"
        )

    def _state_of(self, sys_name: str):
        """Resolve a system name to its GuiSystemState (works for the active
        system, where the model holds the live state, and for any other
        system stored in main_window._all_states)."""
        # The model itself only knows the active state; reach into the
        # main_window via the parent chain.
        if sys_name == getattr(self._model.state, "name", None):
            return self._model.state
        win = self.window()
        all_states = getattr(win, "_all_states", None)
        if isinstance(all_states, dict):
            return all_states.get(sys_name)
        return None

    def _node_name(self, sys_name: str, node_idx: int) -> str:
        st = self._state_of(sys_name)
        if st is not None and 0 <= node_idx < len(st.nodes):
            return st.nodes[node_idx].name
        return f"Node {node_idx}"

    def _populate_bus_combo(self, combo: QComboBox, sys_name: str,
                             current_endpoint) -> None:
        st = self._state_of(sys_name)
        combo.blockSignals(True)
        combo.clear()
        if st is not None:
            selected_idx = -1
            target_id = current_endpoint.element_id if current_endpoint else None
            for i, (bid, bus) in enumerate(st.buses.items()):
                combo.addItem(f"{bid}  ({bus.voltage_kv:g} kV)", bid)
                if bid == target_id:
                    selected_idx = i
            if selected_idx >= 0:
                combo.setCurrentIndex(selected_idx)
        combo.blockSignals(False)

    # ------------------------------------------------------------------
    def _on_bus_combo_changed(self, side: str) -> None:
        if self._updating or self._current_id is None:
            return
        link = self._find(self._current_id)
        if link is None:
            return
        combo = self._from_bus_combo if side == "from" else self._to_bus_combo
        new_bus_id = combo.currentData()
        if not new_bus_id:
            return
        owner_sys = link.from_system if side == "from" else link.to_system
        st = self._state_of(owner_sys)
        if st is None:
            return
        bus = st.buses.get(new_bus_id)
        if bus is None:
            return
        if side == "from":
            link.from_node = bus.parent_node
            link.from_endpoint = EndpointRef("bus", new_bus_id)
        else:
            link.to_node = bus.parent_node
            link.to_endpoint = EndpointRef("bus", new_bus_id)
        # Re-propagate voltage / frequency and re-compute length from the
        # new endpoint geometry. Refresh the three inherited widgets
        # inline (avoid calling load_element here — load_element itself
        # triggers propagate, which would recurse).
        win = self.window()
        if win is not None:
            if hasattr(win, "_propagate_islink_bus_properties"):
                win._propagate_islink_bus_properties(self._current_id)
            if hasattr(win, "_auto_update_islink_length"):
                win._auto_update_islink_length(self._current_id)
        self._updating = True
        try:
            self._voltage.setValue(float(link.voltage_kv or 0.0))
            ct = getattr(link, "current_type", "AC") or "AC"
            ct_idx = self._current_type.findText(ct)
            if ct_idx >= 0:
                self._current_type.setCurrentIndex(ct_idx)
            freq = getattr(link, "frequency_hz", 50.0) or 50.0
            if ct == "DC":
                freq = 0.0
            self._frequency_hz.setValue(float(freq))
        finally:
            self._updating = False
        self._emit_updated()

    def _on_changed(self) -> None:
        if self._updating or self._current_id is None:
            return
        link = self._find(self._current_id)
        if link is None:
            return
        # Preserve 0.0 as a real value (do NOT coerce to None via
        # ``or None``) — otherwise the next load_element would render
        # the field as the special-value text ("Default" / "Global"),
        # which the user perceived as "not editable".
        link.capacity_mw = float(self._capacity.value())
        link.voltage_kv = float(self._voltage.value())
        link.line_type = self._line_type.currentText() or None
        link.base_impedance = float(self._base_impedance.value())
        link.reactance_per_km = float(self._reactance_per_km.value())
        link.reactance_pu = float(self._reactance_pu.value())
        link.resistance_pu = float(self._resistance_pu.value())
        link.susceptance_pu = float(self._susceptance_pu.value())
        link.num_circuits = int(self._num_circuits.value())
        ct = self._current_type.currentText() or "AC"
        link.current_type = ct
        link.frequency_hz = 0.0 if ct == "DC" else float(self._frequency_hz.value())

        # Economic
        link.investment_cost = float(self._investment_cost.value())
        link.max_investment_mw = float(self._max_investment.value())
        link.cost_per_mw_km = float(self._cost_per_mw_km.value())
        link.loss_factor = float(self._loss_factor.value())
        self._emit_updated()

    def _on_style_changed(self) -> None:
        if self._updating or self._current_id is None:
            return
        link = self._find(self._current_id)
        if link is None:
            return
        # VisualStyleWidget returns a fresh VisualStyle snapshot; copy
        # its fields onto the link.style so any downstream rendering
        # picks up the change without rebinding the dataclass attr.
        new_style = self._style_widget.get_style()
        for attr in ("color", "width", "opacity", "size", "icon", "dash"):
            if hasattr(new_style, attr) and hasattr(link.style, attr):
                setattr(link.style, attr, getattr(new_style, attr))
        self._emit_updated()

    def _emit_updated(self) -> None:
        if self._current_id is None:
            return
        try:
            self._model.interSystemLinkUpdated.emit(self._current_id)
        except Exception:
            pass
        self.linkChanged.emit(self._current_id)

    # ------------------------------------------------------------------
    def stop_editing(self) -> None:
        """Disable trace editing (called on deselection)."""
        if self._editing_trace and self._current_id:
            self._edit_trace_btn.setChecked(False)

    def _on_edit_trace_toggled(self, checked: bool) -> None:
        if self._current_id is None:
            return
        self._editing_trace = checked
        self.editTraceToggled.emit(self._current_id, checked)

    def _on_delete(self) -> None:
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
            self.linkDeleteRequested.emit(self._current_id)
