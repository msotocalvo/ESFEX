"""Development zone properties form."""

from __future__ import annotations

import math

from PySide6.QtCore import Signal
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import GuiModel
from esfex.visualization.i18n import tr
from esfex.visualization.panels.visual_style_widget import VisualStyleWidget


def _polygon_area_km2(polygon) -> float:
    """Approximate polygon area on Earth's surface (km²).

    Uses the shoelace formula on spherical coordinates (surveyor's formula).
    """
    if len(polygon) < 3:
        return 0.0
    R = 6371.0
    coords = [(math.radians(p.lat), math.radians(p.lng)) for p in polygon]
    n = len(coords)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        k = (i + 2) % n
        area += (coords[k][1] - coords[i][1]) * math.sin(coords[j][0])
    return abs(area * R * R / 2.0)


class ZoneForm(QWidget):
    """Property editor for a development zone."""

    zoneChanged = Signal(int)
    zoneDeleteRequested = Signal(int)
    editPolygonToggled = Signal(int, bool)  # zone_index, enabled

    def __init__(self, model: GuiModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._current_idx: int | None = None
        self._updating = False
        self._editing_polygon = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._system_label = QLabel("")
        self._system_label.setObjectName("headerLabel")
        outer.addWidget(self._system_label)

        # --- Properties ---
        form = QFormLayout()

        self._name = QLineEdit()
        self._name.editingFinished.connect(self._on_changed)
        form.addRow(tr("zone_form.name"), self._name)

        self._technology = QComboBox()
        self._technology.addItems([
            "Solar", "Wind", "Battery", "Hydro", "Biomass",
            "Hydrogen", "Gas", "Nuclear",
        ])
        self._technology.setEditable(True)
        self._technology.currentTextChanged.connect(self._on_changed)
        form.addRow(tr("zone_form.technology"), self._technology)

        self._node_combo = QComboBox()
        self._node_combo.addItem(tr("zone_form.none_auto"), -1)
        self._node_combo.currentIndexChanged.connect(self._on_changed)
        form.addRow(tr("zone_form.node"), self._node_combo)

        self._area_label = QLabel(tr("zone_form.area_default"))
        form.addRow(tr("zone_form.polygon"), self._area_label)

        outer.addLayout(form)

        self._notes = QTextEdit()
        self._notes.setMaximumHeight(50)
        self._notes.setPlaceholderText(tr("zone_form.notes_placeholder"))
        outer.addWidget(self._notes)

        # --- Interconnection ---
        intercon_group = QGroupBox(tr("zone_form.group_interconnection"))
        intercon_layout = QFormLayout()

        self._line_cost = QDoubleSpinBox()
        self._line_cost.setRange(0, 1e8)
        self._line_cost.setDecimals(3)
        self._line_cost.setValue(1500.0)
        self._line_cost.editingFinished.connect(self._on_changed)
        intercon_layout.addRow(tr("zone_form.line_cost"), self._line_cost)

        self._transformer_cost = QDoubleSpinBox()
        self._transformer_cost.setRange(0, 1e8)
        self._transformer_cost.setDecimals(3)
        self._transformer_cost.setValue(50000.0)
        self._transformer_cost.editingFinished.connect(self._on_changed)
        intercon_layout.addRow(tr("zone_form.transformer_cost"), self._transformer_cost)

        self._target_bus_combo = QComboBox()
        self._target_bus_combo.addItem(tr("zone_form.auto_nearest"), -1)
        self._target_bus_combo.currentIndexChanged.connect(self._on_changed)
        intercon_layout.addRow(tr("zone_form.target_bus"), self._target_bus_combo)

        self._distance_label = QLabel(tr("zone_form.distance_default"))
        intercon_layout.addRow("", self._distance_label)

        self._intercon_cost_label = QLabel(tr("zone_form.total_default"))
        intercon_layout.addRow("", self._intercon_cost_label)

        intercon_group.setLayout(intercon_layout)
        outer.addWidget(intercon_group)

        # --- Allowed Technologies ---
        tech_group = QGroupBox(tr("zone_form.group_technologies"))
        tech_layout = QVBoxLayout()

        self._tech_table = QTableWidget(0, 3)
        self._tech_table.setHorizontalHeaderLabels([
            "", tr("zone_form.col_technology"), tr("zone_form.col_max_invest"),
        ])
        self._tech_table.setMaximumHeight(300)
        self._tech_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows,
        )
        header = self._tech_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._tech_table.setColumnWidth(0, 30)
        self._tech_table.setColumnWidth(2, 90)
        self._tech_table.verticalHeader().setVisible(False)
        tech_layout.addWidget(self._tech_table)

        self._exclusive_cb = QCheckBox(tr("zone_form.exclusive"))
        self._exclusive_cb.setToolTip(tr("zone_form.exclusive_tooltip"))
        self._exclusive_cb.toggled.connect(self._on_changed)
        tech_layout.addWidget(self._exclusive_cb)

        tech_group.setLayout(tech_layout)
        outer.addWidget(tech_group)

        # --- Polygon edit / delete buttons ---
        btn_row = QHBoxLayout()

        self._edit_polygon_btn = QPushButton(tr("zone_form.edit_polygon"))
        self._edit_polygon_btn.setCheckable(True)
        self._edit_polygon_btn.setObjectName("editTraceButton")
        self._edit_polygon_btn.toggled.connect(self._on_edit_polygon_toggled)
        btn_row.addWidget(self._edit_polygon_btn)

        self._delete_btn = QPushButton(tr("zone_form.delete_zone"))
        self._delete_btn.setObjectName("deleteButton")
        self._delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self._delete_btn)

        outer.addLayout(btn_row)

        # --- Appearance ---
        self._style_widget = VisualStyleWidget(
            show_color=True, show_opacity=True,
            show_size=False,
        )
        self._style_widget.styleChanged.connect(self._on_changed)
        outer.addWidget(self._style_widget)

        outer.addStretch()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def load_element(self, element_id: str):
        # Strip system prefix if present (e.g. "Cuba/5" → "5")
        raw_id = element_id.rsplit("/", 1)[-1] if "/" in element_id else element_id
        try:
            idx = int(raw_id)
        except ValueError:
            return
        if idx >= len(self._model.state.development_zones):
            return

        # Disable editing on previous zone if active
        if self._editing_polygon and self._current_idx is not None:
            self._edit_polygon_btn.setChecked(False)

        self._current_idx = idx
        zone = self._model.state.development_zones[idx]
        self._system_label.setText(tr("zone_form.system_label", name=self._model.state.name))

        self._updating = True
        self._name.setText(zone.name)

        # Populate node combo
        self._node_combo.blockSignals(True)
        self._node_combo.clear()
        self._node_combo.addItem(tr("zone_form.none_auto"), -1)
        for nd in self._model.state.nodes:
            self._node_combo.addItem(tr("zone_form.node_combo_fmt", idx=nd.index, name=nd.name), nd.index)
        if zone.node is not None:
            combo_idx = self._node_combo.findData(zone.node)
            if combo_idx >= 0:
                self._node_combo.setCurrentIndex(combo_idx)
        self._node_combo.blockSignals(False)

        tech_idx = self._technology.findText(zone.technology)
        if tech_idx >= 0:
            self._technology.setCurrentIndex(tech_idx)
        else:
            self._technology.setCurrentText(zone.technology)
        self._notes.setPlainText(zone.notes or "")

        self._update_area(zone)

        # Interconnection fields
        self._line_cost.setValue(zone.line_cost_per_mw_km)
        self._transformer_cost.setValue(zone.transformer_cost_per_mw)

        # Populate target bus combo
        self._target_bus_combo.blockSignals(True)
        self._target_bus_combo.clear()
        self._target_bus_combo.addItem(tr("zone_form.auto_nearest"), -1)
        for bus in self._model.state.buses.values():
            self._target_bus_combo.addItem(
                f"Bus {bus.bus_id} (Node {bus.parent_node})", bus.parent_node,
            )
        if zone.target_bus_override is not None:
            combo_idx = self._target_bus_combo.findData(zone.target_bus_override)
            if combo_idx >= 0:
                self._target_bus_combo.setCurrentIndex(combo_idx)
        self._target_bus_combo.blockSignals(False)

        # Populate allowed technologies table
        self._tech_table.blockSignals(True)
        self._tech_table.setRowCount(0)
        techs = list(self._model.state.technologies.values())
        self._tech_table.setRowCount(len(techs))
        for row, tech in enumerate(techs):
            # Column 0: checkbox (centered)
            cb = QCheckBox()
            cb.setChecked(tech.tech_id in zone.allowed_technologies)
            cb.toggled.connect(self._on_tech_table_changed)
            cb_widget = QWidget()
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.addWidget(cb)
            cb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            self._tech_table.setCellWidget(row, 0, cb_widget)

            # Column 1: technology name (read-only)
            name_item = QTableWidgetItem(f"{tech.name} ({tech.category})")
            name_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable,
            )
            name_item.setData(Qt.ItemDataRole.UserRole, tech.tech_id)
            self._tech_table.setItem(row, 1, name_item)

            # Column 2: max invest spinbox
            spin = QDoubleSpinBox()
            spin.setRange(0, 1e6)
            spin.setDecimals(3)
            spin.setSuffix(" MW")
            spin.setSpecialValueText(tr("zone_form.unlimited"))
            max_inv = zone.allowed_technologies.get(tech.tech_id, 0.0)
            spin.setValue(max_inv)
            spin.editingFinished.connect(self._on_tech_table_changed)
            self._tech_table.setCellWidget(row, 2, spin)
        self._tech_table.blockSignals(False)

        self._exclusive_cb.blockSignals(True)
        self._exclusive_cb.setChecked(zone.exclusive)
        self._exclusive_cb.blockSignals(False)

        self._update_intercon_display(zone)
        from esfex.visualization.theme import get_zone_colors
        tech_color = get_zone_colors().get(zone.technology, "#2ecc71")
        self._style_widget.set_default_color(tech_color)
        self._style_widget.load_style(zone.style)
        self._updating = False

    def update_area_display(self):
        """Refresh area label from current zone polygon."""
        if self._current_idx is None:
            return
        if self._current_idx >= len(self._model.state.development_zones):
            return
        zone = self._model.state.development_zones[self._current_idx]
        self._update_area(zone)

    def stop_editing(self):
        """Disable polygon editing if active (e.g. on deselection)."""
        if self._editing_polygon:
            self._edit_polygon_btn.setChecked(False)

    # ------------------------------------------------------------------
    # Area
    # ------------------------------------------------------------------

    def _update_area(self, zone):
        area = _polygon_area_km2(zone.polygon)
        n = len(zone.polygon)
        if area < 1.0:
            self._area_label.setText(tr("zone_form.area_m2", n=n, area=f"{area * 1e6:.0f}"))
        else:
            self._area_label.setText(tr("zone_form.area_km2", n=n, area=f"{area:.2f}"))

    # ------------------------------------------------------------------
    # Polygon editing
    # ------------------------------------------------------------------

    def _on_edit_polygon_toggled(self, checked: bool):
        if self._current_idx is None:
            return
        self._editing_polygon = checked
        self.editPolygonToggled.emit(self._current_idx, checked)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def _on_delete(self):
        if self._current_idx is None:
            return
        zone = self._model.state.development_zones[self._current_idx]
        from esfex.visualization.panels._dialogs import confirm_delete
        if confirm_delete(
            self,
            tr("zone_form.confirm_delete_title"),
            tr("zone_form.confirm_delete_msg", name=zone.name),
        ):
            # Stop editing before deleting
            if self._editing_polygon:
                self._edit_polygon_btn.setChecked(False)
            self.zoneDeleteRequested.emit(self._current_idx)

    # ------------------------------------------------------------------
    # Property changes
    # ------------------------------------------------------------------

    def _on_changed(self):
        if self._updating or self._current_idx is None:
            return
        self._model.checkpoint()
        zone = self._model.state.development_zones[self._current_idx]
        zone.name = self._name.text()
        node_data = self._node_combo.currentData()
        zone.node = node_data if node_data is not None and node_data >= 0 else None
        old_tech = zone.technology
        zone.technology = self._technology.currentText()
        zone.notes = self._notes.toPlainText() or None
        zone.style = self._style_widget.get_style()
        # Update style color when technology changes
        if zone.technology != old_tech:
            from esfex.visualization.theme import get_zone_colors
            tech_color = get_zone_colors().get(zone.technology, "#2ecc71")
            zone.style.color = tech_color
            self._style_widget.set_default_color(tech_color)
            self._updating = True
            self._style_widget.load_style(zone.style)
            self._updating = False
        # Interconnection
        zone.line_cost_per_mw_km = self._line_cost.value()
        zone.transformer_cost_per_mw = self._transformer_cost.value()
        bus_data = self._target_bus_combo.currentData()
        zone.target_bus_override = bus_data if bus_data is not None and bus_data >= 0 else None
        zone.exclusive = self._exclusive_cb.isChecked()
        self._update_intercon_display(zone)
        self.zoneChanged.emit(self._current_idx)

    def _on_tech_table_changed(self, *_args):
        if self._updating or self._current_idx is None:
            return
        zone = self._model.state.development_zones[self._current_idx]
        zone.allowed_technologies = {}
        for row in range(self._tech_table.rowCount()):
            cb_widget = self._tech_table.cellWidget(row, 0)
            cb = cb_widget.findChild(QCheckBox) if cb_widget else None
            if cb and cb.isChecked():
                name_item = self._tech_table.item(row, 1)
                tech_id = name_item.data(Qt.ItemDataRole.UserRole)
                spin = self._tech_table.cellWidget(row, 2)
                max_inv = spin.value() if isinstance(spin, QDoubleSpinBox) else 0.0
                zone.allowed_technologies[tech_id] = max_inv
        self.zoneChanged.emit(self._current_idx)

    # ------------------------------------------------------------------
    # Interconnection display
    # ------------------------------------------------------------------

    def _update_intercon_display(self, zone):
        """Update distance and total cost labels based on zone polygon and target bus."""
        if not zone.polygon or not self._model.state.nodes:
            self._distance_label.setText(tr("zone_form.distance_default"))
            self._intercon_cost_label.setText(tr("zone_form.total_default"))
            return

        # Compute zone centroid
        clat = sum(p.lat for p in zone.polygon) / len(zone.polygon)
        clng = sum(p.lng for p in zone.polygon) / len(zone.polygon)

        # Nodes are abstract (no coordinates) — distance is not computable here
        dist = 0.0

        total = zone.line_cost_per_mw_km * dist + zone.transformer_cost_per_mw
        self._distance_label.setText(tr("zone_form.distance_val", v=f"{dist:.1f}"))
        self._intercon_cost_label.setText(tr("zone_form.total_val", v=f"{total:,.0f}"))

    @staticmethod
    def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2
             + math.cos(math.radians(lat1))
             * math.cos(math.radians(lat2))
             * math.sin(dlon / 2) ** 2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
