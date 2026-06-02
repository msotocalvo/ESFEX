"""Node properties form with General and Demand tabs."""

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.i18n import tr
from esfex.visualization.panels.word_wrap_header import WordWrapHeaderView

try:
    import pandas as pd
except ImportError:
    pd = None

from esfex.visualization.data.gui_model import (
    RENEWABLE_FUELS,
    GuiDemandSector,
    GuiModel,
    GuiNodeDemand,
    GuiNonElectricDemand,
    NodeTechnology,
)


class NodeForm(QWidget):
    """Property editor for a single node (two tabs: General + Demand)."""

    nodeChanged = Signal(int)
    centroidPickRequested = Signal(int)  # node index

    def __init__(self, model: GuiModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._current_node: int | None = None
        self._multi_ids: list[str] | None = None
        self._updating = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._system_label = QLabel("")
        outer.addWidget(self._system_label)

        self._tabs = QTabWidget()
        outer.addWidget(self._tabs)

        # ── Tab 0: General ────────────────────────────────────────
        self._build_general_tab()

        # ── Tab 1: Demand ─────────────────────────────────────────
        self._build_demand_tab()

    # ==============================================================
    # General tab construction
    # ==============================================================

    def _build_general_tab(self):
        content = QWidget()
        form = QFormLayout(content)

        self._name = QLineEdit()
        self._name.editingFinished.connect(self._on_changed)
        form.addRow(tr("node_form.name"), self._name)

        # ── Centroid ──────────────────────────────────────
        centroid_group = QGroupBox(tr("node_form.centroid"))
        centroid_layout = QFormLayout(centroid_group)

        self._centroid_lat = QDoubleSpinBox()
        self._centroid_lat.setRange(-90.0, 90.0)
        self._centroid_lat.setDecimals(6)
        self._centroid_lat.editingFinished.connect(self._on_changed)
        centroid_layout.addRow(tr("node_form.centroid_lat"), self._centroid_lat)

        self._centroid_lng = QDoubleSpinBox()
        self._centroid_lng.setRange(-180.0, 180.0)
        self._centroid_lng.setDecimals(6)
        self._centroid_lng.editingFinished.connect(self._on_changed)
        centroid_layout.addRow(tr("node_form.centroid_lng"), self._centroid_lng)

        self._pick_on_map_btn = QPushButton(tr("node_form.pick_on_map"))
        self._pick_on_map_btn.clicked.connect(self._on_pick_on_map)
        centroid_layout.addRow(self._pick_on_map_btn)

        form.addRow(centroid_group)

        self._reserve_static = QDoubleSpinBox()
        self._reserve_static.setRange(0, 1e6)
        self._reserve_static.editingFinished.connect(self._on_changed)
        form.addRow(tr("node_form.reserve_static"), self._reserve_static)

        self._reserve_dynamic = QDoubleSpinBox()
        self._reserve_dynamic.setRange(0, 1e6)
        self._reserve_dynamic.editingFinished.connect(self._on_changed)
        form.addRow(tr("node_form.reserve_dynamic"), self._reserve_dynamic)

        self._reserve_duration = QSpinBox()
        self._reserve_duration.setRange(0, 24)
        self._reserve_duration.editingFinished.connect(self._on_changed)
        form.addRow(tr("node_form.reserve_duration"), self._reserve_duration)

        self._losses = QDoubleSpinBox()
        self._losses.setRange(0, 1)
        self._losses.setDecimals(4)
        self._losses.editingFinished.connect(self._on_changed)
        form.addRow(tr("node_form.losses"), self._losses)

        self._invest_cost = QDoubleSpinBox()
        self._invest_cost.setRange(0, 1e9)
        self._invest_cost.editingFinished.connect(self._on_changed)
        form.addRow(tr("node_form.invest_cost"), self._invest_cost)

        self._invest_max = QDoubleSpinBox()
        self._invest_max.setRange(0, 1e6)
        self._invest_max.editingFinished.connect(self._on_changed)
        form.addRow(tr("node_form.invest_max"), self._invest_max)

        # ── Technologies ──────────────────────────────────
        tech_group = QGroupBox(tr("node_form.technologies"))
        tech_outer = QVBoxLayout(tech_group)

        tech_btn_row = QHBoxLayout()
        self._tech_combo = QComboBox()
        self._tech_combo.setMinimumWidth(100)
        tech_btn_row.addWidget(self._tech_combo)
        self._tech_add_btn = QPushButton(tr("node_form.add_btn"))
        self._tech_add_btn.clicked.connect(self._on_add_technology)
        tech_btn_row.addWidget(self._tech_add_btn)
        self._tech_remove_btn = QPushButton(tr("node_form.remove_btn"))
        self._tech_remove_btn.clicked.connect(self._on_remove_technology)
        tech_btn_row.addWidget(self._tech_remove_btn)
        tech_btn_row.addStretch()
        tech_outer.addLayout(tech_btn_row)

        self._tech_table = QTableWidget(0, 5)
        self._tech_table.setHorizontalHeader(WordWrapHeaderView(self._tech_table))
        self._tech_table.setHorizontalHeaderLabels([
            tr("node_form.tech_name"),
            tr("node_form.tech_category"),
            tr("node_form.tech_existing"),
            tr("node_form.tech_invest_cost"),
            tr("node_form.tech_max_invest"),
        ])
        self._tech_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch,
        )
        self._tech_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows,
        )
        self._tech_table.cellChanged.connect(self._on_tech_table_changed)
        tech_outer.addWidget(self._tech_table)

        form.addRow(tech_group)

        self._tabs.addTab(content, tr("node_form.tab_general"))

    # ==============================================================
    # Demand tab construction
    # ==============================================================

    def _build_demand_tab(self):
        content = QWidget()
        vbox = QVBoxLayout(content)

        # ── Electric Demand ───────────────────────────────
        demand_group = QGroupBox(tr("node_form.electric_demand"))
        demand_layout = QVBoxLayout(demand_group)

        path_row = QHBoxLayout()
        self._demand_path = QLineEdit()
        self._demand_path.setReadOnly(True)
        self._demand_path.setPlaceholderText(tr("node_form.no_csv"))
        path_row.addWidget(self._demand_path)
        self._demand_load_btn = QPushButton(tr("node_form.load_csv"))
        self._demand_load_btn.clicked.connect(self._on_load_demand_csv)
        path_row.addWidget(self._demand_load_btn)
        demand_layout.addLayout(path_row)

        self._demand_hours_label = QLabel(tr("node_form.hours"))
        demand_layout.addWidget(self._demand_hours_label)
        self._demand_peak_label = QLabel(tr("node_form.peak"))
        demand_layout.addWidget(self._demand_peak_label)
        self._demand_total_label = QLabel(tr("node_form.total"))
        demand_layout.addWidget(self._demand_total_label)

        vbox.addWidget(demand_group)

        # ── Demand Sectors ────────────────────────────────
        sector_def_group = QGroupBox(tr("node_form.demand_sectors"))
        sdl = QVBoxLayout(sector_def_group)
        sdl.setContentsMargins(6, 6, 6, 6)
        sdl.setSpacing(4)

        sec_btn_row = QHBoxLayout()
        self._sector_id_input = QLineEdit()
        self._sector_id_input.setPlaceholderText(tr("node_form.sector_id_placeholder"))
        sec_btn_row.addWidget(self._sector_id_input)
        self._sector_add_btn = QPushButton(tr("node_form.add"))
        self._sector_add_btn.clicked.connect(self._on_add_sector)
        sec_btn_row.addWidget(self._sector_add_btn)
        self._sector_remove_btn = QPushButton(tr("node_form.remove"))
        self._sector_remove_btn.clicked.connect(self._on_remove_sector)
        sec_btn_row.addWidget(self._sector_remove_btn)
        sdl.addLayout(sec_btn_row)

        self._sector_def_table = QTableWidget(0, 5)
        self._sector_def_table.setHorizontalHeader(WordWrapHeaderView(self._sector_def_table))
        self._sector_def_table.setHorizontalHeaderLabels([
            tr("node_form.flexible"), tr("node_form.flex_ratio"), tr("node_form.criticality"),
            tr("node_form.delay_tol"), tr("node_form.price_sens"),
        ])
        self._sector_def_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows,
        )
        self._sector_def_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch,
        )
        self._sector_def_table.setMaximumHeight(150)
        self._sector_def_table.cellChanged.connect(self._on_sector_def_table_changed)
        sdl.addWidget(self._sector_def_table)

        vbox.addWidget(sector_def_group)

        # ── Sector Distribution (per-node fractions) ──────
        sector_dist_group = QGroupBox(tr("node_form.sector_dist"))
        sector_dist_layout = QVBoxLayout(sector_dist_group)

        self._sector_empty_label = QLabel(tr("node_form.no_sectors"))
        sector_dist_layout.addWidget(self._sector_empty_label)

        self._sector_dist_table = QTableWidget(1, 0)
        self._sector_dist_table.setHorizontalHeader(WordWrapHeaderView(self._sector_dist_table))
        self._sector_dist_table.setMaximumHeight(60)
        self._sector_dist_table.verticalHeader().setVisible(False)
        self._sector_dist_table.cellChanged.connect(self._on_sector_dist_changed)
        sector_dist_layout.addWidget(self._sector_dist_table)

        vbox.addWidget(sector_dist_group)

        # ── Non-Electric Demand Types ─────────────────────
        ned_type_group = QGroupBox(tr("node_form.non_electric_demand"))
        ntl = QVBoxLayout(ned_type_group)
        ntl.setContentsMargins(6, 6, 6, 6)
        ntl.setSpacing(4)

        ned_btn_row = QHBoxLayout()
        self._ned_fuel_combo = QComboBox()
        self._ned_fuel_combo.setMinimumWidth(100)
        ned_btn_row.addWidget(self._ned_fuel_combo)
        self._ned_add_btn = QPushButton(tr("node_form.add"))
        self._ned_add_btn.clicked.connect(self._on_add_ned_type)
        ned_btn_row.addWidget(self._ned_add_btn)
        self._ned_remove_btn = QPushButton(tr("node_form.remove"))
        self._ned_remove_btn.clicked.connect(self._on_remove_ned_type)
        ned_btn_row.addWidget(self._ned_remove_btn)
        ntl.addLayout(ned_btn_row)

        self._ned_type_table = QTableWidget(0, 7)
        self._ned_type_table.setHorizontalHeader(WordWrapHeaderView(self._ned_type_table))
        self._ned_type_table.setHorizontalHeaderLabels([
            tr("node_form.unit"), tr("node_form.demand"), tr("node_form.flexible"),
            tr("node_form.flex_ratio"), tr("node_form.criticality"),
            tr("node_form.delay_tol"), tr("node_form.price_sens"),
        ])
        self._ned_type_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows,
        )
        self._ned_type_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch,
        )
        self._ned_type_table.setMaximumHeight(150)
        self._ned_type_table.cellChanged.connect(self._on_ned_type_table_changed)
        ntl.addWidget(self._ned_type_table)

        vbox.addWidget(ned_type_group)
        vbox.addStretch()

        self._tabs.addTab(content, tr("node_form.tab_demand"))

    # ==============================================================
    # Electric demand CSV loading
    # ==============================================================

    def _on_load_demand_csv(self):
        if pd is None:
            QMessageBox.warning(
                self, tr("messages.missing_dep_title"),
                tr("messages.missing_dep_pandas"),
            )
            return

        if self._current_node is None:
            return

        path, _ = QFileDialog.getOpenFileName(
            self, tr("node_form.load_demand_title"), "",
            tr("node_form.csv_filter"),
        )
        if not path:
            return

        node = self._model.get_node(self._current_node)
        if node is None:
            return

        try:
            if path.endswith((".xlsx", ".xls")):
                df = pd.read_excel(path, header=None)
            else:
                df = pd.read_csv(path, header=None)
        except Exception as exc:
            QMessageBox.warning(
                self, tr("messages.read_error_title"),
                tr("messages.read_error_msg", e=exc),
            )
            return

        if df.shape[1] == 1:
            series = df.iloc[:, 0].astype(float)
        else:
            col_idx = node.index
            if col_idx >= df.shape[1]:
                QMessageBox.warning(
                    self, tr("messages.column_mismatch_title"),
                    tr("messages.column_mismatch_msg", idx=col_idx, cols=df.shape[1]),
                )
                return
            series = df.iloc[:, col_idx].astype(float)

        num_hours = len(series)

        for other in self._model.state.nodes:
            if other.index == node.index:
                continue
            if (other.demand.data is not None
                    and other.demand.num_hours != num_hours):
                QMessageBox.warning(
                    self, tr("messages.hour_mismatch_title"),
                    tr("messages.hour_mismatch_msg",
                       n1=num_hours, name=other.name, idx=other.index, n2=other.demand.num_hours),
                )
                return

        data_list = series.tolist()
        peak = float(series.max())
        total = float(series.sum())

        node.demand = GuiNodeDemand(
            csv_path=path,
            data=data_list,
            num_hours=num_hours,
            peak_mw=peak,
            total_mwh=total,
        )

        # The path is stored per-node on ``node.demand.csv_path``; the
        # serializer re-derives the system-level ``demand_paths`` list
        # from each node, so nothing else needs updating here.

        self._update_demand_display(node.demand)
        self._model.nodeUpdated.emit(self._current_node)

    def _update_demand_display(self, demand: GuiNodeDemand):
        if demand.data is not None and demand.csv_path:
            self._demand_path.setText(os.path.basename(demand.csv_path))
            self._demand_path.setToolTip(demand.csv_path)
            self._demand_hours_label.setText(tr("node_form.hours_val", n=demand.num_hours))
            self._demand_peak_label.setText(tr("node_form.peak_val", v=f"{demand.peak_mw:,.1f}"))
            self._demand_total_label.setText(tr("node_form.total_val", v=f"{demand.total_mwh:,.1f}"))
        else:
            self._demand_path.clear()
            self._demand_hours_label.setText(tr("node_form.hours"))
            self._demand_peak_label.setText(tr("node_form.peak"))
            self._demand_total_label.setText(tr("node_form.total"))

    # ==============================================================
    # Demand Sectors (definitions)
    # ==============================================================

    def _populate_sector_def_table(self):
        self._sector_def_table.blockSignals(True)
        self._sector_def_table.setRowCount(0)
        for sid, sec in self._model.state.demand_sectors.items():
            row = self._sector_def_table.rowCount()
            self._sector_def_table.insertRow(row)
            self._sector_def_table.setVerticalHeaderItem(
                row, QTableWidgetItem(sid),
            )
            self._sector_def_table.setItem(
                row, 0, QTableWidgetItem("1" if sec.is_flexible else "0"),
            )
            self._sector_def_table.setItem(
                row, 1, QTableWidgetItem(f"{sec.flexibility_ratio:.2f}"),
            )
            self._sector_def_table.setItem(
                row, 2, QTableWidgetItem(sec.criticality),
            )
            self._sector_def_table.setItem(
                row, 3, QTableWidgetItem(str(sec.delay_tolerance)),
            )
            self._sector_def_table.setItem(
                row, 4, QTableWidgetItem(f"{sec.price_sensitivity:.2f}"),
            )
        self._sector_def_table.blockSignals(False)

    def _on_add_sector(self):
        sid = self._sector_id_input.text().strip()
        if not sid or sid in self._model.state.demand_sectors:
            return
        self._model.state.demand_sectors[sid] = GuiDemandSector(sector_id=sid)
        self._populate_sector_def_table()
        self._sector_id_input.clear()
        if self._current_node is not None:
            self._populate_sector_dist_table(self._current_node)

    def _on_remove_sector(self):
        row = self._sector_def_table.currentRow()
        if row < 0:
            return
        header = self._sector_def_table.verticalHeaderItem(row)
        if header is None:
            return
        sid = header.text()
        self._model.state.demand_sectors.pop(sid, None)
        for node_dist in self._model.state.sector_distribution.values():
            node_dist.pop(sid, None)
        self._populate_sector_def_table()
        if self._current_node is not None:
            self._populate_sector_dist_table(self._current_node)

    def _on_sector_def_table_changed(self, row: int, col: int):
        if self._updating:
            return
        header = self._sector_def_table.verticalHeaderItem(row)
        if header is None:
            return
        sid = header.text()
        sec = self._model.state.demand_sectors.get(sid)
        if sec is None:
            return
        item = self._sector_def_table.item(row, col)
        text = item.text() if item else ""
        if col == 0:
            sec.is_flexible = text.lower() in ("1", "true", "yes")
        elif col == 1:
            try:
                sec.flexibility_ratio = float(text)
            except ValueError:
                pass
        elif col == 2:
            try:
                if text in ("critical", "high", "medium", "low"):
                    sec.criticality = text
            except Exception:
                pass
        elif col == 3:
            try:
                sec.delay_tolerance = int(text)
            except ValueError:
                pass
        elif col == 4:
            try:
                sec.price_sensitivity = float(text)
            except ValueError:
                pass

    # ==============================================================
    # Sector distribution (per-node fractions)
    # ==============================================================

    def _populate_sector_dist_table(self, node_idx: int):
        self._sector_dist_table.blockSignals(True)
        sectors = self._model.state.demand_sectors
        if not sectors:
            self._sector_empty_label.show()
            self._sector_dist_table.hide()
            self._sector_dist_table.blockSignals(False)
            return

        self._sector_empty_label.hide()
        self._sector_dist_table.show()

        sector_ids = list(sectors.keys())
        self._sector_dist_table.setColumnCount(len(sector_ids))
        self._sector_dist_table.setHorizontalHeaderLabels(sector_ids)
        self._sector_dist_table.setRowCount(1)

        dist = self._model.state.sector_distribution.get(node_idx, {})
        for col, sid in enumerate(sector_ids):
            val = dist.get(sid, 0.0)
            item = QTableWidgetItem(f"{val:.3f}")
            self._sector_dist_table.setItem(0, col, item)

        self._sector_dist_table.blockSignals(False)

    def _on_sector_dist_changed(self, row: int, col: int):
        if self._updating or self._current_node is None:
            return
        sectors = list(self._model.state.demand_sectors.keys())
        if col >= len(sectors):
            return
        sid = sectors[col]
        item = self._sector_dist_table.item(row, col)
        try:
            val = float(item.text()) if item else 0.0
        except ValueError:
            val = 0.0

        dist = self._model.state.sector_distribution
        if self._current_node not in dist:
            dist[self._current_node] = {}
        dist[self._current_node][sid] = val
        self._model.nodeUpdated.emit(self._current_node)

    # ==============================================================
    # Non-Electric Demand Types (definitions)
    # ==============================================================

    def _populate_ned_fuel_combo(self):
        self._ned_fuel_combo.clear()
        existing_fuels = {
            ned.fuel for ned in self._model.state.non_electric_demand.values()
        }
        for fid, fuel in self._model.state.fuels.items():
            if fuel.name not in existing_fuels and fid not in RENEWABLE_FUELS:
                self._ned_fuel_combo.addItem(fuel.name, fid)

    def _populate_ned_type_table(self):
        self._ned_type_table.blockSignals(True)
        self._ned_type_table.setRowCount(0)
        node_idx = self._current_node or 0
        for did, ned in self._model.state.non_electric_demand.items():
            row = self._ned_type_table.rowCount()
            self._ned_type_table.insertRow(row)
            header_item = QTableWidgetItem(ned.fuel or did)
            header_item.setData(Qt.ItemDataRole.UserRole, did)
            self._ned_type_table.setVerticalHeaderItem(row, header_item)
            self._ned_type_table.setItem(row, 0, QTableWidgetItem(ned.unit))
            demand_val = ned.demand[node_idx] if node_idx < len(ned.demand) else 0
            self._ned_type_table.setItem(
                row, 1, QTableWidgetItem(str(demand_val)),
            )
            self._ned_type_table.setItem(
                row, 2, QTableWidgetItem("1" if ned.is_flexible else "0"),
            )
            self._ned_type_table.setItem(
                row, 3, QTableWidgetItem(f"{ned.flexibility_ratio:.2f}"),
            )
            self._ned_type_table.setItem(
                row, 4, QTableWidgetItem(ned.criticality),
            )
            self._ned_type_table.setItem(
                row, 5, QTableWidgetItem(str(ned.delay_tolerance)),
            )
            self._ned_type_table.setItem(
                row, 6, QTableWidgetItem(f"{ned.price_sensitivity:.2f}"),
            )
        self._ned_type_table.blockSignals(False)

    def _on_add_ned_type(self):
        idx = self._ned_fuel_combo.currentIndex()
        if idx < 0:
            return
        fuel_id = self._ned_fuel_combo.currentData()
        fuel_obj = self._model.state.fuels.get(fuel_id)
        if not fuel_obj:
            return
        num_nodes = len(self._model.state.nodes)
        self._model.state.non_electric_demand[fuel_id] = GuiNonElectricDemand(
            demand_id=fuel_id,
            fuel=fuel_obj.name,
            unit=fuel_obj.unit or "",
            demand=[0] * num_nodes,
        )
        self._populate_ned_fuel_combo()
        self._populate_ned_type_table()

    def _on_remove_ned_type(self):
        row = self._ned_type_table.currentRow()
        if row < 0:
            return
        header = self._ned_type_table.verticalHeaderItem(row)
        if header is None:
            return
        did = header.data(Qt.ItemDataRole.UserRole)
        self._model.state.non_electric_demand.pop(did, None)
        self._populate_ned_fuel_combo()
        self._populate_ned_type_table()

    def _on_ned_type_table_changed(self, row: int, col: int):
        if self._updating:
            return
        header = self._ned_type_table.verticalHeaderItem(row)
        if header is None:
            return
        did = header.data(Qt.ItemDataRole.UserRole)
        ned = self._model.state.non_electric_demand.get(did)
        if ned is None:
            return
        item = self._ned_type_table.item(row, col)
        text = item.text() if item else ""
        if col == 0:
            ned.unit = text
        elif col == 1:
            # Per-node annual demand
            if self._current_node is not None:
                try:
                    val = int(text)
                except ValueError:
                    val = 0
                while len(ned.demand) <= self._current_node:
                    ned.demand.append(0)
                ned.demand[self._current_node] = val
        elif col == 2:
            ned.is_flexible = text.lower() in ("1", "true", "yes")
        elif col == 3:
            try:
                ned.flexibility_ratio = float(text)
            except ValueError:
                pass
        elif col == 4:
            try:
                if text in ("critical", "high", "medium", "low"):
                    ned.criticality = text
            except Exception:
                pass
        elif col == 5:
            try:
                ned.delay_tolerance = int(text)
            except ValueError:
                pass
        elif col == 6:
            try:
                ned.price_sensitivity = float(text)
            except ValueError:
                pass

    # ==============================================================
    # Technologies
    # ==============================================================

    _TECH_CATEGORIES = [
        "generation", "storage", "fuel", "transmission",
        "fuel_transport", "transformation",
    ]

    def _populate_tech_combo(self, node_idx: int):
        """Fill the technology combo from generators/batteries at this node."""
        self._tech_combo.clear()
        names = set()
        for inst in self._model.state.generators.values():
            if inst.node == node_idx:
                names.add(inst.name)
        for inst in self._model.state.batteries.values():
            if inst.node == node_idx:
                names.add(inst.name)
        for inst in self._model.state.electrolyzers.values():
            if inst.node == node_idx:
                names.add(inst.name)
        # Exclude already-added technologies
        node = self._model.get_node(node_idx)
        existing = set()
        if node:
            existing = {t.name for t in node.technologies}
        for n in sorted(names - existing):
            self._tech_combo.addItem(n)

    def _populate_tech_table(self, node):
        self._tech_table.blockSignals(True)
        self._tech_table.setRowCount(0)

        for tech in node.technologies:
            row = self._tech_table.rowCount()
            self._tech_table.insertRow(row)
            self._tech_table.setItem(row, 0, QTableWidgetItem(tech.name))
            combo = QComboBox()
            combo.addItems(self._TECH_CATEGORIES)
            combo.setCurrentText(tech.category)
            combo.currentTextChanged.connect(self._on_tech_table_changed)
            self._tech_table.setCellWidget(row, 1, combo)
            self._tech_table.setItem(
                row, 2, QTableWidgetItem(f"{tech.existing_capacity:.1f}"),
            )
            self._tech_table.setItem(
                row, 3, QTableWidgetItem(f"{tech.invest_cost:.0f}"),
            )
            self._tech_table.setItem(
                row, 4, QTableWidgetItem(f"{tech.invest_max:.1f}"),
            )

        self._tech_table.blockSignals(False)

    def _on_add_technology(self):
        if self._current_node is None:
            return
        node = self._model.get_node(self._current_node)
        if node is None:
            return
        name = self._tech_combo.currentText()
        if not name:
            return
        # Infer category from equipment type
        category = "generation"
        for inst in self._model.state.batteries.values():
            if inst.node == self._current_node and inst.name == name:
                category = "storage"
                break
        for inst in self._model.state.electrolyzers.values():
            if inst.node == self._current_node and inst.name == name:
                category = "fuel"
                break

        tech = NodeTechnology(name=name, category=category)
        node.technologies.append(tech)
        self._populate_tech_table(node)
        self._populate_tech_combo(self._current_node)
        self._model.nodeUpdated.emit(self._current_node)

    def _on_remove_technology(self):
        if self._current_node is None:
            return
        node = self._model.get_node(self._current_node)
        if node is None:
            return
        row = self._tech_table.currentRow()
        if 0 <= row < len(node.technologies):
            node.technologies.pop(row)
            self._populate_tech_table(node)
            self._populate_tech_combo(self._current_node)
            self._model.nodeUpdated.emit(self._current_node)

    def _on_tech_table_changed(self, *_args):
        if self._updating or self._current_node is None:
            return
        node = self._model.get_node(self._current_node)
        if node is None:
            return
        node.technologies.clear()
        for row in range(self._tech_table.rowCount()):
            name_item = self._tech_table.item(row, 0)
            cat_widget = self._tech_table.cellWidget(row, 1)
            existing_item = self._tech_table.item(row, 2)
            cost_item = self._tech_table.item(row, 3)
            max_item = self._tech_table.item(row, 4)
            tech = NodeTechnology(
                name=name_item.text() if name_item else "",
                category=cat_widget.currentText() if cat_widget else "generation",
                existing_capacity=float(existing_item.text() or 0) if existing_item else 0,
                invest_cost=float(cost_item.text() or 0) if cost_item else 0,
                invest_max=float(max_item.text() or 0) if max_item else 0,
            )
            node.technologies.append(tech)
        self._model.nodeUpdated.emit(self._current_node)

    # ==============================================================
    # Centroid pick-on-map
    # ==============================================================

    def _on_pick_on_map(self):
        if self._current_node is not None:
            self.centroidPickRequested.emit(self._current_node)

    def set_centroid(self, lat: float, lng: float):
        """Called externally after the user picks a point on the map."""
        self._updating = True
        self._centroid_lat.setValue(lat)
        self._centroid_lng.setValue(lng)
        self._updating = False
        self._on_changed()

    # ==============================================================
    # load_element / _on_changed
    # ==============================================================

    def _field_map(self):
        return [
            ("name", self._name),
            ("centroid_lat", self._centroid_lat),
            ("centroid_lng", self._centroid_lng),
            ("reserve_static", self._reserve_static),
            ("reserve_dynamic", self._reserve_dynamic),
            ("reserve_duration", self._reserve_duration),
            ("losses", self._losses),
            ("transference_invest_cost", self._invest_cost),
            ("transference_invest_max", self._invest_max),
        ]

    def load_elements(self, element_ids: list[str]):
        """Load multiple nodes for batch editing (scalar fields only)."""
        from esfex.visualization.panels.multi_edit import collect_attr, set_widget_value

        nodes = [self._model.get_node(int(eid)) for eid in element_ids]
        nodes = [n for n in nodes if n is not None]
        if not nodes:
            return
        self._multi_ids = element_ids
        self._current_node = int(element_ids[0])
        self._system_label.setText(tr("node_form.n_nodes_selected", n=len(element_ids)))
        self._updating = True

        for attr, w in self._field_map():
            set_widget_value(w, collect_attr(nodes, attr))

        # Hide tabs that don't make sense for multi-edit
        self._tabs.setTabEnabled(1, False)  # Demand tab
        # Clear tech table
        self._tech_table.setRowCount(0)
        self._tech_combo.clear()

        self._updating = False

    def load_element(self, element_id: str):
        """Populate the form with data from the model."""
        self._multi_ids = None
        self._tabs.setTabEnabled(1, True)
        self._current_node = int(element_id)
        node = self._model.get_node(self._current_node)
        if node is None:
            # Defensive: if the active system doesn't have this index,
            # clear the form instead of leaving stale data on screen.
            self._current_node = None
            self._updating = True
            self._update_demand_display(GuiNodeDemand())
            self._updating = False
            return

        self._system_label.setText(tr("node_form.system_label", name=self._model.state.name))
        self._updating = True

        # General tab
        self._name.setText(node.name)
        self._centroid_lat.setValue(node.centroid_lat)
        self._centroid_lng.setValue(node.centroid_lng)
        self._reserve_static.setValue(node.reserve_static)
        self._reserve_dynamic.setValue(node.reserve_dynamic)
        self._reserve_duration.setValue(node.reserve_duration)
        self._losses.setValue(node.losses)
        self._invest_cost.setValue(node.transference_invest_cost)
        self._invest_max.setValue(node.transference_invest_max)

        self._populate_tech_table(node)
        self._populate_tech_combo(self._current_node)

        # Demand tab
        self._update_demand_display(node.demand)
        self._populate_sector_def_table()
        self._populate_sector_dist_table(self._current_node)
        self._populate_ned_fuel_combo()
        self._populate_ned_type_table()

        self._updating = False

    def _on_changed(self):
        if self._updating or self._current_node is None:
            return
        self._model.checkpoint()
        from esfex.visualization.panels.multi_edit import widget_is_mixed

        ids = self._multi_ids or ([str(self._current_node)] if self._current_node is not None else [])
        nodes = [self._model.get_node(int(eid)) for eid in ids]
        nodes = [n for n in nodes if n is not None]
        if not nodes:
            return

        kwargs: dict = {}
        for attr, w in self._field_map():
            if widget_is_mixed(w):
                continue
            if isinstance(w, QLineEdit):
                kwargs[attr] = w.text()
            else:
                kwargs[attr] = w.value()

        for eid in ids:
            nidx = int(eid)
            self._model.update_node(nidx, **kwargs)
            self.nodeChanged.emit(nidx)
