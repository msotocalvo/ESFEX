"""System-level settings form with vertically stacked sections."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import GuiModel
from esfex.visualization.i18n import tr


class SystemForm(QWidget):
    """Property editor for per-system settings (settings, penalties, DC PF)."""

    systemSettingsChanged = Signal()
    systemRenamed = Signal(str, str)  # old_name, new_name

    def __init__(self, model: GuiModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._updating = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._header_label = QLabel("")
        self._header_label.setObjectName("headerLabel")
        outer.addWidget(self._header_label)

        # Editable system name
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(tr("system_form.name_label")))
        self._name_edit = QLineEdit()
        self._name_edit.editingFinished.connect(self._on_name_changed)
        name_row.addWidget(self._name_edit)
        outer.addLayout(name_row)

        # Use outer layout directly (parent already provides scrolling)
        scroll_layout = outer
        scroll_layout.setContentsMargins(6, 6, 6, 6)
        scroll_layout.setSpacing(8)

        # ── Section 1: General ──
        grp_general = QGroupBox(tr("system_form.tab_general"))
        gl = QFormLayout(grp_general)
        gl.setContentsMargins(6, 6, 6, 6)
        gl.setSpacing(4)

        self._demand_scale = QDoubleSpinBox()
        self._demand_scale.setRange(0.01, 100)
        self._demand_scale.setDecimals(3)
        self._demand_scale.setSingleStep(0.1)
        self._demand_scale.editingFinished.connect(self._on_changed)
        gl.addRow(tr("system_form.demand_scale"), self._demand_scale)

        self._discount_rate = QDoubleSpinBox()
        self._discount_rate.setRange(0, 1)
        self._discount_rate.setDecimals(4)
        self._discount_rate.setSingleStep(0.01)
        self._discount_rate.editingFinished.connect(self._on_changed)
        gl.addRow(tr("system_form.discount_rate"), self._discount_rate)

        self._base_lcoe = QDoubleSpinBox()
        self._base_lcoe.setRange(0, 1e6)
        self._base_lcoe.setDecimals(2)
        self._base_lcoe.editingFinished.connect(self._on_changed)
        gl.addRow(tr("system_form.base_lcoe"), self._base_lcoe)

        self._target_re = QDoubleSpinBox()
        self._target_re.setRange(0, 1)
        self._target_re.setDecimals(3)
        self._target_re.setSingleStep(0.05)
        self._target_re.editingFinished.connect(self._on_changed)
        gl.addRow(tr("system_form.target_re"), self._target_re)

        self._min_increment = QDoubleSpinBox()
        self._min_increment.setRange(0, 1)
        self._min_increment.setDecimals(3)
        self._min_increment.setSingleStep(0.01)
        self._min_increment.editingFinished.connect(self._on_changed)
        gl.addRow(tr("system_form.min_increment"), self._min_increment)

        self._max_increment = QDoubleSpinBox()
        self._max_increment.setRange(0, 1)
        self._max_increment.setDecimals(3)
        self._max_increment.setSingleStep(0.01)
        self._max_increment.editingFinished.connect(self._on_changed)
        gl.addRow(tr("system_form.max_increment"), self._max_increment)

        self._loss_demand_thr = QDoubleSpinBox()
        self._loss_demand_thr.setRange(0, 1)
        self._loss_demand_thr.setDecimals(4)
        self._loss_demand_thr.editingFinished.connect(self._on_changed)
        gl.addRow(tr("system_form.loss_demand_thr"), self._loss_demand_thr)

        self._inertia_limit_thr = QDoubleSpinBox()
        self._inertia_limit_thr.setRange(0, 1)
        self._inertia_limit_thr.setDecimals(4)
        self._inertia_limit_thr.editingFinished.connect(self._on_changed)
        gl.addRow(tr("system_form.inertia_limit_thr"), self._inertia_limit_thr)

        self._sim_rooftop = QCheckBox(tr("common.enable"))
        self._sim_rooftop.toggled.connect(self._on_changed)
        gl.addRow(tr("system_form.sim_rooftop"), self._sim_rooftop)

        # CO2 Budget fields (moved from CO2 tab)
        self._co2_enabled = QCheckBox(tr("system_form.enable_co2"))
        self._co2_enabled.toggled.connect(self._on_changed)
        gl.addRow("", self._co2_enabled)

        self._co2_annual = QDoubleSpinBox()
        self._co2_annual.setRange(0, 1e12)
        self._co2_annual.setDecimals(0)
        self._co2_annual.editingFinished.connect(self._on_changed)
        gl.addRow(tr("system_form.co2_annual"), self._co2_annual)

        scroll_layout.addWidget(grp_general)

        # ── Section 2: Cost Limits ──
        grp_costs = QGroupBox(tr("system_form.tab_cost_limits"))
        cl = QFormLayout(grp_costs)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.setSpacing(4)

        self._max_annual_cost = QDoubleSpinBox()
        self._max_annual_cost.setRange(0, 1e15)
        self._max_annual_cost.setDecimals(0)
        self._max_annual_cost.editingFinished.connect(self._on_changed)
        cl.addRow(tr("system_form.max_annual_cost"), self._max_annual_cost)

        self._max_npv_penalty = QDoubleSpinBox()
        self._max_npv_penalty.setRange(0, 1e12)
        self._max_npv_penalty.setDecimals(0)
        self._max_npv_penalty.editingFinished.connect(self._on_changed)
        cl.addRow(tr("system_form.max_npv_penalty"), self._max_npv_penalty)

        self._max_decom_cost = QDoubleSpinBox()
        self._max_decom_cost.setRange(0, 1e12)
        self._max_decom_cost.setDecimals(0)
        self._max_decom_cost.editingFinished.connect(self._on_changed)
        cl.addRow(tr("system_form.max_decom_cost"), self._max_decom_cost)

        self._force_replacement = QDoubleSpinBox()
        self._force_replacement.setRange(-1e12, 1e12)
        self._force_replacement.setDecimals(0)
        self._force_replacement.editingFinished.connect(self._on_changed)
        cl.addRow(tr("system_form.force_replacement"), self._force_replacement)

        self._life_ext_factor = QDoubleSpinBox()
        self._life_ext_factor.setRange(0, 10)
        self._life_ext_factor.setDecimals(3)
        self._life_ext_factor.editingFinished.connect(self._on_changed)
        cl.addRow(tr("system_form.life_ext_factor"), self._life_ext_factor)

        scroll_layout.addWidget(grp_costs)

        # ── Section 3: Penalties ──
        grp_penalties = QGroupBox(tr("system_form.tab_penalties"))
        pl = QFormLayout(grp_penalties)
        pl.setContentsMargins(6, 6, 6, 6)
        pl.setSpacing(4)

        self._penalty_widgets: dict[str, QDoubleSpinBox] = {}
        penalty_fields = [
            ("loss_of_load", tr("system_form.penalty_loss_of_load"), 0, 1e12),
            ("loss_of_reserve_static", tr("system_form.penalty_reserve_static"), 0, 1e9),
            ("loss_of_reserve_dynamic", tr("system_form.penalty_reserve_dynamic"), 0, 1e9),
            ("loss_of_inertia", tr("system_form.penalty_inertia"), 0, 1e9),
            ("transfer_margin", tr("system_form.penalty_transfer"), 0, 1e9),
            ("curtailment", tr("system_form.penalty_curtailment"), 0, 1e9),
            ("max_curtailment_ratio", tr("system_form.penalty_curtailment_ratio"), 0, 1),
            ("curtailment_cost", tr("system_form.penalty_curtailment_cost"), 0, 1e9),
            ("curtailment_excess_penalty", tr("system_form.penalty_curtailment_excess"), 0, 1e9),
            ("re_excess_penalty", tr("system_form.penalty_re_excess"), 0, 1e9),
            ("rooftop_curtailment", tr("system_form.penalty_rooftop_curt"), 0, 1e9),
            ("co2_cost", tr("system_form.penalty_co2_cost"), 0, 1e9),
            ("co2_budget_violation", tr("system_form.penalty_co2_budget"), 0, 1e9),
            ("fre_penetration_loss", tr("system_form.penalty_fre"), 0, 1e9),
            ("ev_loss", tr("system_form.penalty_ev"), 0, 1e9),
            ("loss_of_fuel_supply", tr("system_form.penalty_fuel_supply"), 0, 1e9),
            ("coupling_slack_penalty", tr("system_form.penalty_coupling_slack"), 0, 1e9),
            ("transport_congestion", tr("system_form.penalty_transport"), 0, 1e9),
            ("storage_violation", tr("system_form.penalty_storage"), 0, 1e9),
            ("non_electric_demand_loss", tr("system_form.penalty_non_elec"), 0, 1e9),
        ]
        for field_name, label, lo, hi in penalty_fields:
            spin = QDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setDecimals(2 if hi > 1 else 4)
            spin.editingFinished.connect(self._on_changed)
            pl.addRow(label, spin)
            self._penalty_widgets[field_name] = spin

        # Load Criticality Penalties (moved from CO2 tab)
        self._crit_widgets: dict[str, QDoubleSpinBox] = {}
        for level in ["critical", "high", "medium", "low"]:
            spin = QDoubleSpinBox()
            spin.setRange(0, 1e9)
            spin.setDecimals(2)
            spin.editingFinished.connect(self._on_changed)
            pl.addRow(tr("system_form.criticality_level", level=level.capitalize()), spin)
            self._crit_widgets[level] = spin

        scroll_layout.addWidget(grp_penalties)

        # NOTE: the "Power Flow" section (OPF formulation + DC/AC settings)
        # moved to Global Settings — the OPF formulation is a model-wide
        # choice (multi-system configs merge into one network solved with a
        # single formulation), so it does not belong per-system.

        # Add stretch at bottom
        scroll_layout.addStretch()

    def load_element(self, element_id: str = ""):
        """Load system settings. element_id is ignored (one per system)."""
        self._updating = True
        s = self._model.state.settings
        p = self._model.state.penalties

        self._header_label.setText(tr("system_form.system_label", name=self._model.state.name))
        self._name_edit.setText(self._model.state.name)

        # General
        self._demand_scale.setValue(s.demand_scale)
        self._discount_rate.setValue(s.discount_rate)
        self._base_lcoe.setValue(s.base_lcoe)
        self._target_re.setValue(s.target_re_penetration)
        self._min_increment.setValue(s.min_annual_increment)
        self._max_increment.setValue(s.max_annual_increment)
        self._loss_demand_thr.setValue(s.loss_demand_threshold)
        self._inertia_limit_thr.setValue(s.inertia_limit_threshold)
        self._sim_rooftop.setChecked(s.sim_rooftop)

        # CO2 Budget (now in General section)
        self._co2_enabled.setChecked(s.co2_budget_enabled)
        self._co2_annual.setValue(s.co2_annual_budget)

        # Cost Limits
        self._max_annual_cost.setValue(s.max_annual_system_cost)
        self._max_npv_penalty.setValue(s.max_npv_penalty_per_mw)
        self._max_decom_cost.setValue(s.max_decommission_cost_per_mw)
        self._force_replacement.setValue(s.force_replacement)
        self._life_ext_factor.setValue(s.life_extension_cost_factor)

        # Penalties
        for field_name, spin in self._penalty_widgets.items():
            spin.setValue(getattr(p, field_name))

        # Criticality (now in Penalties section)
        for level, spin in self._crit_widgets.items():
            attr_name = f"criticality_{level}"
            spin.setValue(getattr(p, attr_name))

        # Power Flow moved to Global Settings (model-wide OPF formulation).

        self._updating = False

    def _on_name_changed(self):
        if self._updating:
            return
        new_name = self._name_edit.text().strip()
        old_name = self._model.state.name
        if not new_name or new_name == old_name:
            return
        self._model.state.name = new_name
        self._header_label.setText(tr("system_form.system_label", name=new_name))
        self.systemRenamed.emit(old_name, new_name)

    def _on_changed(self):
        if self._updating:
            return
        self._model.checkpoint()
        s = self._model.state.settings
        p = self._model.state.penalties

        # General
        s.demand_scale = self._demand_scale.value()
        s.discount_rate = self._discount_rate.value()
        s.base_lcoe = self._base_lcoe.value()
        s.target_re_penetration = self._target_re.value()
        s.min_annual_increment = self._min_increment.value()
        s.max_annual_increment = self._max_increment.value()
        s.loss_demand_threshold = self._loss_demand_thr.value()
        s.inertia_limit_threshold = self._inertia_limit_thr.value()
        s.sim_rooftop = self._sim_rooftop.isChecked()

        # CO2 Budget (now in settings)
        s.co2_budget_enabled = self._co2_enabled.isChecked()
        s.co2_annual_budget = self._co2_annual.value()

        # Cost Limits
        s.max_annual_system_cost = self._max_annual_cost.value()
        s.max_npv_penalty_per_mw = self._max_npv_penalty.value()
        s.max_decommission_cost_per_mw = self._max_decom_cost.value()
        s.force_replacement = self._force_replacement.value()
        s.life_extension_cost_factor = self._life_ext_factor.value()

        # Penalties
        for field_name, spin in self._penalty_widgets.items():
            setattr(p, field_name, spin.value())

        # Criticality (now in penalties)
        for level, spin in self._crit_widgets.items():
            attr_name = f"criticality_{level}"
            setattr(p, attr_name, spin.value())

        # Power Flow moved to Global Settings (model-wide OPF formulation).

        self._model.systemSettingsUpdated.emit()
        self.systemSettingsChanged.emit()
