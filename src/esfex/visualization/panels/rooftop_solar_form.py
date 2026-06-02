"""Rooftop solar configuration form."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import GuiModel, GuiRooftopSolar
from esfex.visualization.i18n import tr
from esfex.visualization.panels.word_wrap_header import WordWrapHeaderView


class RooftopSolarForm(QWidget):
    """Property editor for rooftop solar configuration."""

    rooftopChanged = Signal()

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

        # --- Settings ---
        grp_set = QGroupBox(tr("rooftop_form.group_settings"))
        fl = QFormLayout(grp_set)
        fl.setContentsMargins(6, 6, 6, 6)
        fl.setSpacing(4)

        self._adoption_scenario = QComboBox()
        self._adoption_scenario.addItem(tr("rooftop_form.opt_low"), "low")
        self._adoption_scenario.addItem(tr("rooftop_form.opt_medium"), "medium")
        self._adoption_scenario.addItem(tr("rooftop_form.opt_high"), "high")
        self._adoption_scenario.currentIndexChanged.connect(self._on_changed)
        fl.addRow(tr("rooftop_form.adoption_scenario"), self._adoption_scenario)

        self._weather = QComboBox()
        self._weather.addItem(tr("rooftop_form.opt_low"), "low")
        self._weather.addItem(tr("rooftop_form.opt_normal"), "normal")
        self._weather.addItem(tr("rooftop_form.opt_high"), "high")
        self._weather.currentIndexChanged.connect(self._on_changed)
        fl.addRow(tr("rooftop_form.weather"), self._weather)

        self._seed = QSpinBox()
        self._seed.setRange(0, 999999)
        self._seed.editingFinished.connect(self._on_changed)
        fl.addRow(tr("rooftop_form.seed"), self._seed)

        self._perf_ratio = QDoubleSpinBox()
        self._perf_ratio.setRange(0, 1)
        self._perf_ratio.setDecimals(3)
        self._perf_ratio.editingFinished.connect(self._on_changed)
        fl.addRow(tr("rooftop_form.perf_ratio"), self._perf_ratio)

        self._degradation = QDoubleSpinBox()
        self._degradation.setRange(0, 0.1)
        self._degradation.setDecimals(4)
        self._degradation.editingFinished.connect(self._on_changed)
        fl.addRow(tr("rooftop_form.degradation"), self._degradation)

        self._cost_kw = QDoubleSpinBox()
        self._cost_kw.setRange(0, 1e6)
        self._cost_kw.setDecimals(2)
        self._cost_kw.editingFinished.connect(self._on_changed)
        fl.addRow(tr("rooftop_form.cost_kw"), self._cost_kw)

        self._cost_reduction = QDoubleSpinBox()
        self._cost_reduction.setRange(0, 1)
        self._cost_reduction.setDecimals(4)
        self._cost_reduction.editingFinished.connect(self._on_changed)
        fl.addRow(tr("rooftop_form.cost_reduction"), self._cost_reduction)

        self._om_cost = QDoubleSpinBox()
        self._om_cost.setRange(0, 1e6)
        self._om_cost.setDecimals(2)
        self._om_cost.editingFinished.connect(self._on_changed)
        fl.addRow(tr("rooftop_form.om_cost"), self._om_cost)

        self._base_year = QSpinBox()
        self._base_year.setRange(2000, 2100)
        self._base_year.editingFinished.connect(self._on_changed)
        fl.addRow(tr("rooftop_form.base_year"), self._base_year)

        self._target_year = QSpinBox()
        self._target_year.setRange(2000, 2100)
        self._target_year.editingFinished.connect(self._on_changed)
        fl.addRow(tr("rooftop_form.target_year"), self._target_year)

        outer.addWidget(grp_set)

        # --- Per-node parameters ---
        grp_nodes = QGroupBox(tr("rooftop_form.group_per_node"))
        nl = QVBoxLayout(grp_nodes)

        self._node_table = QTableWidget()
        self._node_table.setHorizontalHeader(WordWrapHeaderView(self._node_table))
        self._node_table.setColumnCount(3)
        self._node_table.setHorizontalHeaderLabels([
            tr("rooftop_form.systems_node"), tr("rooftop_form.avg_size"), tr("rooftop_form.init_adoption"),
        ])
        self._node_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._node_table.cellChanged.connect(self._on_node_table_changed)
        nl.addWidget(self._node_table)

        outer.addWidget(grp_nodes)

        # --- Adoption limits ---
        grp_adopt = QGroupBox(tr("rooftop_form.group_adoption"))
        al = QVBoxLayout(grp_adopt)

        self._adopt_table = QTableWidget()
        self._adopt_table.setHorizontalHeader(WordWrapHeaderView(self._adopt_table))
        self._adopt_table.setColumnCount(2)
        self._adopt_table.setHorizontalHeaderLabels([tr("rooftop_form.max_adoption"), tr("rooftop_form.adoption_rate")])
        self._adopt_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._adopt_table.cellChanged.connect(self._on_adopt_table_changed)
        al.addWidget(self._adopt_table)

        outer.addWidget(grp_adopt)

        outer.addStretch()

    def _ensure_rooftop(self) -> GuiRooftopSolar:
        if self._model.state.rooftop_solar is None:
            self._model.state.rooftop_solar = GuiRooftopSolar()
        return self._model.state.rooftop_solar

    def load_element(self, element_id: str = ""):
        """Load rooftop solar config. element_id is ignored."""
        self._updating = True
        rt = self._ensure_rooftop()
        nodes = self._model.state.nodes

        self._header_label.setText(tr("investment_form.system_label", name=self._model.state.name))

        idx = self._adoption_scenario.findData(rt.adoption_scenario)
        if idx >= 0:
            self._adoption_scenario.setCurrentIndex(idx)
        idx = self._weather.findData(rt.weather_variability)
        if idx >= 0:
            self._weather.setCurrentIndex(idx)
        self._seed.setValue(rt.simulation_seed)
        self._perf_ratio.setValue(rt.performance_ratio)
        self._degradation.setValue(rt.degradation_rate)
        self._cost_kw.setValue(rt.cost_per_kw)
        self._cost_reduction.setValue(rt.cost_reduction_rate)
        self._om_cost.setValue(rt.o_and_m_cost)
        self._base_year.setValue(rt.base_year)
        self._target_year.setValue(rt.target_year)

        # Per-node
        num_nodes = len(nodes)
        while len(rt.systems_per_node) < num_nodes:
            rt.systems_per_node.append(0)
        while len(rt.avg_system_size) < num_nodes:
            rt.avg_system_size.append(0.0)
        while len(rt.initial_adoption) < num_nodes:
            rt.initial_adoption.append(0.0)

        self._node_table.setRowCount(num_nodes)
        for i in range(num_nodes):
            self._node_table.setVerticalHeaderItem(
                i, QTableWidgetItem(nodes[i].name)
            )
            self._node_table.setItem(
                i, 0, QTableWidgetItem(str(rt.systems_per_node[i]))
            )
            self._node_table.setItem(
                i, 1, QTableWidgetItem(f"{rt.avg_system_size[i]}")
            )
            self._node_table.setItem(
                i, 2, QTableWidgetItem(f"{rt.initial_adoption[i]}")
            )

        # Adoption limits
        scenarios = sorted(
            set(list(rt.max_adoption.keys()) + list(rt.adoption_rates.keys()))
            or ["low", "medium", "high"]
        )
        self._adopt_table.setRowCount(len(scenarios))
        for i, sc in enumerate(scenarios):
            self._adopt_table.setVerticalHeaderItem(
                i, QTableWidgetItem(sc)
            )
            self._adopt_table.setItem(
                i, 0, QTableWidgetItem(f"{rt.max_adoption.get(sc, 0.0)}")
            )
            self._adopt_table.setItem(
                i, 1, QTableWidgetItem(f"{rt.adoption_rates.get(sc, 0.0)}")
            )

        self._updating = False

    def _on_changed(self):
        if self._updating:
            return
        self._model.checkpoint()
        rt = self._ensure_rooftop()
        rt.adoption_scenario = self._adoption_scenario.currentData() or "low"
        rt.weather_variability = self._weather.currentData() or "normal"
        rt.simulation_seed = self._seed.value()
        rt.performance_ratio = self._perf_ratio.value()
        rt.degradation_rate = self._degradation.value()
        rt.cost_per_kw = self._cost_kw.value()
        rt.cost_reduction_rate = self._cost_reduction.value()
        rt.o_and_m_cost = self._om_cost.value()
        rt.base_year = self._base_year.value()
        rt.target_year = self._target_year.value()
        self.rooftopChanged.emit()

    def _on_node_table_changed(self, row: int, col: int):
        if self._updating:
            return
        rt = self._ensure_rooftop()
        item = self._node_table.item(row, col)
        if not item:
            return
        try:
            val = float(item.text())
        except ValueError:
            return
        arrays = [rt.systems_per_node, rt.avg_system_size, rt.initial_adoption]
        if col < len(arrays) and row < len(arrays[col]):
            if col == 0:
                arrays[col][row] = int(val)
            else:
                arrays[col][row] = val
            self.rooftopChanged.emit()

    def _on_adopt_table_changed(self, row: int, col: int):
        if self._updating:
            return
        rt = self._ensure_rooftop()
        header = self._adopt_table.verticalHeaderItem(row)
        if not header:
            return
        sc = header.text()
        item = self._adopt_table.item(row, col)
        if not item:
            return
        try:
            val = float(item.text())
        except ValueError:
            return
        if col == 0:
            rt.max_adoption[sc] = val
        else:
            rt.adoption_rates[sc] = val
        self.rooftopChanged.emit()
