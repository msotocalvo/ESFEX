"""Technology properties form for defining investable technology categories."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import GuiModel
from esfex.visualization.i18n import tr


_CATEGORY_OPTIONS = ["Renewable", "Non-renewable", "Storage", "Electrolyzer"]


class TechnologyForm(QWidget):
    """Property editor for a technology definition."""

    technologyChanged = Signal(str)
    technologyDeleteRequested = Signal(str)

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
        id_grp = QGroupBox(tr("technology_form.group_identity"))
        id_layout = QFormLayout(id_grp)
        id_layout.setContentsMargins(6, 6, 6, 6)
        id_layout.setSpacing(4)

        self._name = QLineEdit()
        self._name.editingFinished.connect(self._on_changed)
        id_layout.addRow(tr("generator_form.name"), self._name)

        self._category = QComboBox()
        self._category.addItems(_CATEGORY_OPTIONS)
        self._category.currentTextChanged.connect(self._on_category_changed)
        id_layout.addRow(tr("technology_form.category"), self._category)

        self._fuel = QComboBox()
        self._fuel.addItem(tr("technology_form.none_fuel"), "")
        self._fuel.currentIndexChanged.connect(self._on_changed)
        id_layout.addRow(tr("generator_form.fuel"), self._fuel)

        # Color picker — a small left-aligned swatch. Height matches the
        # other Identity fields; width is a quarter of the field column
        # since the button only needs to show the color, not text. It is
        # wrapped in a container with a trailing stretch so QFormLayout's
        # field-growth policy can't stretch the button to fill the column.
        self._color_btn = QPushButton()
        self._color_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed,
        )
        self._color_btn.setFixedHeight(self._name.sizeHint().height())
        self._color_btn.setFixedWidth(max(24, self._name.sizeHint().width() // 4))
        self._color_btn.setToolTip("Chart display color")
        self._color_btn.clicked.connect(self._on_color_pick)
        self._color_value: str = ""
        self._update_color_btn_style("")
        _color_row = QWidget()
        _color_row_lay = QHBoxLayout(_color_row)
        _color_row_lay.setContentsMargins(0, 0, 0, 0)
        _color_row_lay.addWidget(self._color_btn)
        _color_row_lay.addStretch(1)
        id_layout.addRow("Color", _color_row)

        outer.addWidget(id_grp)

        # Investment limits/costs are NOT defined here: per-node × per-technology
        # investment is edited in the Investment Portfolio (the only place that
        # reaches the optimizer). This catalog is for the technology definition
        # (category, fuel, efficiency, lifetime, color, element linking).

        # --- Basic Operations ---
        ops_grp = QGroupBox(tr("technology_form.group_basic"))
        ops_layout = QFormLayout(ops_grp)
        ops_layout.setContentsMargins(6, 6, 6, 6)
        ops_layout.setSpacing(4)

        self._life_time = QSpinBox()
        self._life_time.setRange(1, 100)
        self._life_time.setSuffix(" years")
        self._life_time.editingFinished.connect(self._on_changed)
        ops_layout.addRow(tr("technology_form.lifetime"), self._life_time)

        self._degradation_rate = QDoubleSpinBox()
        self._degradation_rate.setRange(0, 1)
        self._degradation_rate.setDecimals(4)
        self._degradation_rate.setSingleStep(0.001)
        self._degradation_rate.editingFinished.connect(self._on_changed)
        ops_layout.addRow(tr("technology_form.degradation_rate"), self._degradation_rate)

        self._eff_at_rated = QDoubleSpinBox()
        self._eff_at_rated.setRange(0, 1)
        self._eff_at_rated.setDecimals(4)
        self._eff_at_rated.setSingleStep(0.01)
        self._eff_at_rated.editingFinished.connect(self._on_changed)
        ops_layout.addRow(tr("technology_form.eff_at_rated"), self._eff_at_rated)

        self._eff_at_min = QDoubleSpinBox()
        self._eff_at_min.setRange(0, 1)
        self._eff_at_min.setDecimals(4)
        self._eff_at_min.setSingleStep(0.01)
        self._eff_at_min.editingFinished.connect(self._on_changed)
        ops_layout.addRow(tr("technology_form.eff_at_min"), self._eff_at_min)

        outer.addWidget(ops_grp)

        # -- Delete button --
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(4, 2, 4, 2)
        self._delete_btn = QPushButton(tr("technology_form.delete_technology"))
        self._delete_btn.setObjectName("deleteButton")
        self._delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self._delete_btn)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        outer.addStretch()

    def _field_map(self):
        return [
            ("name", self._name),
            ("category", self._category),
            ("fuel", self._fuel),
            ("life_time", self._life_time),
            ("degradation_rate", self._degradation_rate),
            ("eff_at_rated", self._eff_at_rated),
            ("eff_at_min", self._eff_at_min),
        ]

    def load_elements(self, element_ids: list[str]):
        """Load multiple technologies for batch editing."""
        from esfex.visualization.panels.multi_edit import collect_attr, set_widget_value

        techs = self._model.state.technologies
        instances = [techs[eid] for eid in element_ids if eid in techs]
        if not instances:
            return
        self._multi_ids = element_ids
        self._current_id = element_ids[0]
        self._header_label.setText(tr("technology_form.n_selected", n=len(element_ids)))
        self._updating = True

        for attr, w in self._field_map():
            val = collect_attr(instances, attr)
            set_widget_value(w, val)

        self._updating = False

    def load_element(self, element_id: str):
        self._multi_ids = None
        self._current_id = element_id
        tech = self._model.state.technologies.get(element_id)
        if not tech:
            return

        self._updating = True
        self._header_label.setText(
            f"{self._model.state.name}  |  {tech.tech_id}"
        )
        self._name.setText(tech.name)
        idx = self._category.findText(tech.category)
        if idx >= 0:
            self._category.setCurrentIndex(idx)

        # Populate fuel combo from system fuels
        self._fuel.blockSignals(True)
        self._fuel.clear()
        self._fuel.addItem(tr("technology_form.none_fuel"), "")
        for fuel in self._model.state.fuels.values():
            self._fuel.addItem(fuel.name, fuel.fuel_id)
        fuel_idx = self._fuel.findData(tech.fuel)
        if fuel_idx < 0:
            fuel_idx = self._fuel.findText(tech.fuel)
        if fuel_idx >= 0:
            self._fuel.setCurrentIndex(fuel_idx)
        self._fuel.blockSignals(False)
        self._life_time.setValue(tech.life_time)
        self._degradation_rate.setValue(tech.degradation_rate)
        self._eff_at_rated.setValue(tech.eff_at_rated)
        self._eff_at_min.setValue(tech.eff_at_min)
        self._update_color_btn_style(tech.style.color or "")
        self._updating = False

    def _update_color_btn_style(self, hex_color: str):
        """Update the color button background to reflect the selected color."""
        self._color_value = hex_color or ""
        if hex_color:
            self._color_btn.setStyleSheet(
                "QPushButton {"
                f" background-color: {hex_color}; border: 1px solid #888;"
                " border-radius: 2px; }"
            )
            self._color_btn.setText("")
        else:
            self._color_btn.setStyleSheet(
                "QPushButton { border: 1px solid #888; border-radius: 2px; }"
            )
            self._color_btn.setText("...")

    def _on_color_pick(self):
        initial = QColor(self._color_value) if self._color_value else QColor("#808080")
        color = QColorDialog.getColor(initial, self, "Technology Color")
        if color.isValid():
            self._update_color_btn_style(color.name())
            self._on_changed()

    def _on_category_changed(self, _text: str):
        if not self._updating:
            self._on_changed()

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
                kwargs[attr] = w.text()
            elif isinstance(w, QComboBox):
                if attr == "fuel":
                    kwargs[attr] = w.currentData() or ""
                else:
                    kwargs[attr] = w.currentText()
            else:
                kwargs[attr] = w.value()

        for eid in ids:
            self._model.update_technology(eid, **kwargs)
            # Update style.color separately (nested field)
            tech = self._model.state.technologies.get(eid)
            if tech:
                tech.style.color = self._color_value or None
            self.technologyChanged.emit(eid)

    def _on_delete(self):
        if self._current_id is None:
            return
        from esfex.visualization.panels._dialogs import confirm_delete
        if confirm_delete(
            self,
            tr("technology_form.confirm_delete_title"),
            tr("technology_form.confirm_delete_msg"),
        ):
            self.technologyDeleteRequested.emit(self._current_id)
