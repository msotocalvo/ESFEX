"""Electrolyzer instance properties form."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import GuiModel
from esfex.visualization.i18n import tr
from esfex.visualization.panels.visual_style_widget import VisualStyleWidget


class ElectrolyzerForm(QWidget):
    """Property editor for an electrolyzer instance."""

    electrolyzerChanged = Signal(str)
    electrolyzerDeleteRequested = Signal(str)

    def __init__(self, model: GuiModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._current_id: str | None = None
        self._multi_ids: list[str] | None = None
        self._updating = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._header_label = QLabel("")
        self._header_label.setObjectName("headerLabel")
        outer.addWidget(self._header_label)

        # --- Identity ---
        grp_id = QGroupBox(tr("electrolyzer_form.group_identity"))
        fl_id = QFormLayout(grp_id)
        fl_id.setContentsMargins(6, 6, 6, 6)
        fl_id.setSpacing(4)

        self._name = QLineEdit()
        self._name.editingFinished.connect(self._on_changed)
        fl_id.addRow(tr("electrolyzer_form.name"), self._name)

        self._technology = QComboBox()
        self._technology.addItems(["PEM", "Alkaline", "SOE"])
        self._technology.currentTextChanged.connect(self._on_changed)
        fl_id.addRow(tr("electrolyzer_form.technology"), self._technology)

        self._fuel = QLineEdit()
        self._fuel.editingFinished.connect(self._on_changed)
        fl_id.addRow(tr("electrolyzer_form.fuel"), self._fuel)

        self._tech_ref_combo = QComboBox()
        self._tech_ref_combo.addItem(tr("electrolyzer_form.none"), "")
        self._tech_ref_combo.currentIndexChanged.connect(self._on_changed)
        fl_id.addRow(tr("electrolyzer_form.technology_ref"), self._tech_ref_combo)

        outer.addWidget(grp_id)

        # --- Capacity ---
        grp_cap = QGroupBox(tr("electrolyzer_form.group_capacity"))
        fl_cap = QFormLayout(grp_cap)
        fl_cap.setContentsMargins(6, 6, 6, 6)
        fl_cap.setSpacing(4)

        self._rated_power = QDoubleSpinBox()
        self._rated_power.setRange(0, 1e6)
        self._rated_power.setDecimals(2)
        self._rated_power.editingFinished.connect(self._on_changed)
        fl_cap.addRow(tr("electrolyzer_form.rated_power"), self._rated_power)

        self._min_power = QDoubleSpinBox()
        self._min_power.setRange(0, 1)
        self._min_power.setDecimals(3)
        self._min_power.editingFinished.connect(self._on_changed)
        fl_cap.addRow(tr("electrolyzer_form.min_power"), self._min_power)

        self._ramp_up = QDoubleSpinBox()
        self._ramp_up.setRange(0, 1)
        self._ramp_up.setDecimals(3)
        self._ramp_up.editingFinished.connect(self._on_changed)
        fl_cap.addRow(tr("electrolyzer_form.ramp_up"), self._ramp_up)

        self._ramp_down = QDoubleSpinBox()
        self._ramp_down.setRange(0, 1)
        self._ramp_down.setDecimals(3)
        self._ramp_down.editingFinished.connect(self._on_changed)
        fl_cap.addRow(tr("electrolyzer_form.ramp_down"), self._ramp_down)

        outer.addWidget(grp_cap)

        # --- Efficiency ---
        grp_eff = QGroupBox(tr("electrolyzer_form.group_efficiency"))
        fl_eff = QFormLayout(grp_eff)
        fl_eff.setContentsMargins(6, 6, 6, 6)
        fl_eff.setSpacing(4)

        self._eff_rated = QDoubleSpinBox()
        self._eff_rated.setRange(0, 1)
        self._eff_rated.setDecimals(3)
        self._eff_rated.editingFinished.connect(self._on_changed)
        fl_eff.addRow(tr("electrolyzer_form.eff_at_rated"), self._eff_rated)

        self._eff_min = QDoubleSpinBox()
        self._eff_min.setRange(0, 1)
        self._eff_min.setDecimals(3)
        self._eff_min.editingFinished.connect(self._on_changed)
        fl_eff.addRow(tr("electrolyzer_form.eff_at_min"), self._eff_min)

        self._energy_per_kg = QDoubleSpinBox()
        self._energy_per_kg.setRange(0, 1000)
        self._energy_per_kg.setDecimals(2)
        self._energy_per_kg.editingFinished.connect(self._on_changed)
        fl_eff.addRow(tr("electrolyzer_form.energy_per_kg"), self._energy_per_kg)

        outer.addWidget(grp_eff)

        # --- Economics ---
        grp_eco = QGroupBox(tr("electrolyzer_form.group_economics"))
        fl_eco = QFormLayout(grp_eco)
        fl_eco.setContentsMargins(6, 6, 6, 6)
        fl_eco.setSpacing(4)

        self._fixed_cost = QDoubleSpinBox()
        self._fixed_cost.setRange(0, 1e9)
        self._fixed_cost.setDecimals(2)
        self._fixed_cost.editingFinished.connect(self._on_changed)
        fl_eco.addRow(tr("electrolyzer_form.fixed_cost"), self._fixed_cost)

        self._variable_cost = QDoubleSpinBox()
        self._variable_cost.setRange(0, 1e9)
        self._variable_cost.setDecimals(4)
        self._variable_cost.editingFinished.connect(self._on_changed)
        fl_eco.addRow(tr("electrolyzer_form.variable_cost"), self._variable_cost)

        self._water_cost = QDoubleSpinBox()
        self._water_cost.setRange(0, 1e6)
        self._water_cost.setDecimals(4)
        self._water_cost.editingFinished.connect(self._on_changed)
        fl_eco.addRow(tr("electrolyzer_form.water_cost"), self._water_cost)

        outer.addWidget(grp_eco)

        # --- Lifecycle ---
        grp_life = QGroupBox(tr("electrolyzer_form.group_lifecycle"))
        fl_life = QFormLayout(grp_life)
        fl_life.setContentsMargins(6, 6, 6, 6)
        fl_life.setSpacing(4)

        self._life_time = QSpinBox()
        self._life_time.setRange(1, 100)
        self._life_time.editingFinished.connect(self._on_changed)
        fl_life.addRow(tr("electrolyzer_form.lifetime"), self._life_time)

        self._initial_age = QSpinBox()
        self._initial_age.setRange(0, 100)
        self._initial_age.editingFinished.connect(self._on_changed)
        fl_life.addRow(tr("electrolyzer_form.initial_age"), self._initial_age)

        self._degradation = QDoubleSpinBox()
        self._degradation.setRange(0, 1)
        self._degradation.setDecimals(4)
        self._degradation.editingFinished.connect(self._on_changed)
        fl_life.addRow(tr("electrolyzer_form.degradation_rate"), self._degradation)

        outer.addWidget(grp_life)

        # -- Appearance --
        self._style_widget = VisualStyleWidget(
            show_color=True, show_size=True,
        )
        self._style_widget.styleChanged.connect(self._on_changed)
        outer.addWidget(self._style_widget)

        outer.addStretch()

        # -- Delete button --
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(4, 2, 4, 2)
        self._delete_btn = QPushButton(tr("electrolyzer_form.delete_electrolyzer"))
        self._delete_btn.setObjectName("deleteButton")
        self._delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self._delete_btn)
        btn_row.addStretch()
        outer.addLayout(btn_row)

    def load_element(self, element_id: str):
        self._multi_ids = None
        self._current_id = element_id
        inst = self._model.state.electrolyzers.get(element_id)
        if not inst:
            return

        self._updating = True

        node_name = ""
        node = self._model.get_node(inst.node)
        if node:
            node_name = node.name
        self._header_label.setText(
            f"{self._model.state.name}  |  {node_name}"
        )

        self._name.setText(inst.name)
        idx = self._technology.findText(inst.technology)
        if idx >= 0:
            self._technology.setCurrentIndex(idx)
        self._fuel.setText(inst.fuel)

        # Populate technology ref combo with Electrolyzer techs
        self._tech_ref_combo.blockSignals(True)
        self._tech_ref_combo.clear()
        self._tech_ref_combo.addItem(tr("electrolyzer_form.none"), "")
        for tech in self._model.state.technologies.values():
            if tech.category == "Electrolyzer":
                self._tech_ref_combo.addItem(tech.name, tech.tech_id)
        tech_idx = self._tech_ref_combo.findData(inst.technology_id or "")
        if tech_idx >= 0:
            self._tech_ref_combo.setCurrentIndex(tech_idx)
        self._tech_ref_combo.blockSignals(False)
        self._rated_power.setValue(inst.rated_power)
        self._min_power.setValue(inst.min_power)
        self._ramp_up.setValue(inst.ramp_up)
        self._ramp_down.setValue(inst.ramp_down)
        self._eff_rated.setValue(inst.eff_at_rated)
        self._eff_min.setValue(inst.eff_at_min)
        self._energy_per_kg.setValue(inst.energy_per_kg_h2)
        self._fixed_cost.setValue(inst.fixed_cost)
        self._variable_cost.setValue(inst.variable_cost)
        self._water_cost.setValue(inst.water_cost)
        self._life_time.setValue(inst.life_time)
        self._initial_age.setValue(inst.initial_age)
        self._degradation.setValue(inst.degradation_rate)
        from esfex.visualization.data.default_colors import ELECTROLYZER
        self._style_widget.set_default_color(ELECTROLYZER)
        self._style_widget.load_style(inst.style)

        self._updating = False

    def _field_map(self):
        return [
            ("name", self._name),
            ("technology", self._technology),
            ("fuel", self._fuel),
            ("rated_power", self._rated_power),
            ("min_power", self._min_power),
            ("ramp_up", self._ramp_up),
            ("ramp_down", self._ramp_down),
            ("eff_at_rated", self._eff_rated),
            ("eff_at_min", self._eff_min),
            ("energy_per_kg_h2", self._energy_per_kg),
            ("fixed_cost", self._fixed_cost),
            ("variable_cost", self._variable_cost),
            ("water_cost", self._water_cost),
            ("life_time", self._life_time),
            ("initial_age", self._initial_age),
            ("degradation_rate", self._degradation),
        ]

    def load_elements(self, element_ids: list[str]):
        from esfex.visualization.panels.multi_edit import collect_attr, set_widget_value
        instances = [self._model.state.electrolyzers[eid] for eid in element_ids
                     if eid in self._model.state.electrolyzers]
        if not instances:
            return
        self._multi_ids = element_ids
        self._current_id = element_ids[0]
        self._header_label.setText(tr("electrolyzer_form.multi_selected", count=len(element_ids)))
        self._updating = True
        for attr, w in self._field_map():
            set_widget_value(w, collect_attr(instances, attr))
        self._updating = False

    def _on_changed(self):
        if self._updating:
            return
        self._model.checkpoint()
        from esfex.visualization.panels.multi_edit import widget_is_mixed

        ids = self._multi_ids or ([self._current_id] if self._current_id else [])
        instances = [self._model.state.electrolyzers[i] for i in ids
                     if i in self._model.state.electrolyzers]
        if not instances:
            return

        kwargs: dict = {}
        for attr, w in self._field_map():
            if widget_is_mixed(w):
                continue
            if isinstance(w, QLineEdit):
                kwargs[attr] = w.text()
            elif isinstance(w, QComboBox):
                kwargs[attr] = w.currentText()
            else:
                kwargs[attr] = w.value()

        if not widget_is_mixed(self._tech_ref_combo):
            kwargs["technology_id"] = self._tech_ref_combo.currentData() or None

        if not self._multi_ids and instances:
            instances[0].style = self._style_widget.get_style()

        for eid in ids:
            self._model.update_electrolyzer(eid, **kwargs)
            self.electrolyzerChanged.emit(eid)

    def _on_delete(self):
        if self._current_id is None:
            return
        from esfex.visualization.panels._dialogs import confirm_delete
        if confirm_delete(
            self,
            tr("electrolyzer_form.confirm_delete_title"),
            tr("electrolyzer_form.confirm_delete_msg", name=self._current_id),
        ):
            self.electrolyzerDeleteRequested.emit(self._current_id)
