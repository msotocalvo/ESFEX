"""Fuel properties form (physical/economic, distinct from fuel source/supply)."""

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
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import GuiModel
from esfex.visualization.i18n import tr


class FuelForm(QWidget):
    """Property editor for a fuel type (FuelConfig)."""

    fuelChanged = Signal(str)
    fuelDeleteRequested = Signal(str)

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

        # --- Properties ---
        grp = QGroupBox(tr("fuel_form.group_properties"))
        layout = QFormLayout(grp)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self._name = QLineEdit()
        self._name.editingFinished.connect(self._on_changed)
        layout.addRow(tr("generator_form.name"), self._name)

        self._unit = QLineEdit()
        self._unit.setPlaceholderText(tr("fuel_form.unit_placeholder"))
        self._unit.editingFinished.connect(self._on_changed)
        layout.addRow(tr("fuel_form.unit"), self._unit)

        self._emission_factor = QDoubleSpinBox()
        self._emission_factor.setRange(0, 1000)
        self._emission_factor.setDecimals(4)
        self._emission_factor.editingFinished.connect(self._on_changed)
        layout.addRow(tr("fuel_form.emission_factor"), self._emission_factor)

        self._energy_content = QDoubleSpinBox()
        self._energy_content.setRange(0, 1e6)
        self._energy_content.setDecimals(4)
        self._energy_content.setSpecialValueText("N/A (renewable)")
        self._energy_content.editingFinished.connect(self._on_changed)
        layout.addRow(tr("fuel_form.energy_content"), self._energy_content)

        self._price_base = QDoubleSpinBox()
        self._price_base.setRange(0, 1e9)
        self._price_base.setDecimals(2)
        self._price_base.editingFinished.connect(self._on_changed)
        layout.addRow(tr("fuel_form.price_base"), self._price_base)

        self._price_growth = QDoubleSpinBox()
        self._price_growth.setRange(-1, 10)
        self._price_growth.setDecimals(4)
        self._price_growth.setSingleStep(0.01)
        self._price_growth.editingFinished.connect(self._on_changed)
        layout.addRow(tr("fuel_form.price_growth"), self._price_growth)

        outer.addWidget(grp)

        # -- Delete button --
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(4, 2, 4, 2)
        self._delete_btn = QPushButton(tr("fuel_form.delete_fuel"))
        self._delete_btn.setObjectName("deleteButton")
        self._delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self._delete_btn)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        outer.addStretch()

    def _field_map(self):
        return [
            ("name", self._name),
            ("unit", self._unit),
            ("emission_factor", self._emission_factor),
            ("energy_content", self._energy_content),
            ("price_base", self._price_base),
            ("price_growth_rate", self._price_growth),
        ]

    def load_elements(self, element_ids: list[str]):
        """Load multiple fuels for batch editing."""
        from esfex.visualization.panels.multi_edit import collect_attr, set_widget_value

        fuels = self._model.state.fuels
        instances = [fuels[eid] for eid in element_ids if eid in fuels]
        if not instances:
            return
        self._multi_ids = element_ids
        self._current_id = element_ids[0]
        self._header_label.setText(tr("fuel_form.n_selected", n=len(element_ids)))
        self._updating = True

        for attr, w in self._field_map():
            val = collect_attr(instances, attr)
            # energy_content and unit can be None
            if attr == "energy_content" and val is None:
                val = 0
            if attr == "unit" and val is None:
                val = ""
            set_widget_value(w, val)

        self._updating = False

    def load_element(self, element_id: str):
        self._multi_ids = None
        self._current_id = element_id
        fuel = self._model.state.fuels.get(element_id)
        if not fuel:
            return

        self._updating = True
        self._header_label.setText(
            f"{self._model.state.name}  |  {fuel.fuel_id}"
        )
        self._name.setText(fuel.name)
        self._unit.setText(fuel.unit or "")
        self._emission_factor.setValue(fuel.emission_factor)
        self._energy_content.setValue(fuel.energy_content or 0)
        self._price_base.setValue(fuel.price_base)
        self._price_growth.setValue(fuel.price_growth_rate)
        self._updating = False

    def _on_changed(self):
        if self._updating or not self._current_id:
            return
        self._model.checkpoint()
        from esfex.visualization.panels.multi_edit import widget_is_mixed

        ids = self._multi_ids or ([self._current_id] if self._current_id else [])
        kwargs: dict = {}
        for attr, w in self._field_map():
            if widget_is_mixed(w):
                continue
            if isinstance(w, QLineEdit):
                val = w.text()
                if attr == "unit":
                    val = val or None
            else:
                val = w.value()
                if attr == "energy_content":
                    val = val or None
            kwargs[attr] = val

        for eid in ids:
            self._model.update_fuel(eid, **kwargs)
            self.fuelChanged.emit(eid)

    def _on_delete(self):
        if self._current_id is None:
            return
        from esfex.visualization.panels._dialogs import confirm_delete
        if confirm_delete(
            self,
            tr("fuel_form.confirm_delete_title"),
            tr("fuel_form.confirm_delete_msg"),
        ):
            self.fuelDeleteRequested.emit(self._current_id)
