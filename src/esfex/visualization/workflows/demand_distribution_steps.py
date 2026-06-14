"""Step widgets for the Demand Distribution Analysis wizard.

Five steps:
1. TargetSelectionStep — pick target nodes (multi-system) & review buses
2. DomainFetchStep — define area & fetch building footprints
3. ClassificationStep — configure building-type rules & preview weights
4. ClusteringStep — run spatial clustering algorithm
5. ReviewApplyStep — review per-node bus ↔ cluster mapping & apply changes
"""

from __future__ import annotations

import json
import math
from typing import Optional

import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.i18n import tr
from esfex.visualization.workflows.demand_analysis import (
    CLUSTER_COLORS,
    DEFAULT_RULES,
    BuildingTypeRule,
    ClusteringWorker,
    classify_buildings,
    compute_classification_summary,
)


def _load_buses_of_node(state, node_index: int):
    """Return buses that participate in demand distribution.

    Connection buses (role='connection') do not carry load and must be
    excluded from demand distribution — their demand_fraction is forced to
    0 by the optimizer regardless. Only ``load`` and ``mixed`` buses are
    candidates for receiving a fraction of the node's demand.
    """
    return [
        b for b in state.buses.values()
        if b.parent_node == node_index and b.role in ("load", "mixed")
    ]


# =====================================================================
# Step 1: Target Selection (multi-node, multi-system)
# =====================================================================


class TargetSelectionStep(QWidget):
    """Select target nodes across systems and review their existing buses."""

    def __init__(self, all_states: dict, parent=None):
        super().__init__(parent)
        self._all_states = all_states  # system_name → GuiSystemState

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("wizard_demand.step1_instruction")))

        # Checkbox tree: systems → nodes
        tree_group = QGroupBox(tr("wizard_demand.select_nodes"))
        tree_lay = QVBoxLayout(tree_group)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels([
            tr("wizard_demand.node_label"),
            tr("wizard_demand.peak_demand"),
            tr("wizard_demand.bus_count"),
        ])
        self._tree.setColumnWidth(0, 250)
        self._tree.itemChanged.connect(self._on_item_changed)
        tree_lay.addWidget(self._tree)
        layout.addWidget(tree_group)

        # Summary of selected nodes + buses
        self._summary_label = QLabel("")
        self._summary_label.setWordWrap(True)
        self._summary_label.setStyleSheet("padding: 8px;")
        layout.addWidget(self._summary_label)

        layout.addStretch()

    def populate(self):
        """Build the tree from all_states."""
        self._tree.blockSignals(True)
        self._tree.clear()
        for sys_name, state in self._all_states.items():
            sys_item = QTreeWidgetItem(self._tree)
            sys_item.setText(0, sys_name)
            sys_item.setFlags(
                sys_item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate
            )
            sys_item.setCheckState(0, Qt.CheckState.Unchecked)

            for node in state.nodes:
                # Only count load/mixed buses — connection buses cannot receive demand
                buses = _load_buses_of_node(state, node.index)
                peak = node.demand.peak_mw if node.demand else 0.0
                node_item = QTreeWidgetItem(sys_item)
                node_item.setText(0, f"{node.name} (Node {node.index})")
                node_item.setText(1, f"{peak:.1f} MW")
                node_item.setText(2, str(len(buses)))
                node_item.setFlags(
                    node_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                )
                node_item.setCheckState(0, Qt.CheckState.Unchecked)
                node_item.setData(0, Qt.ItemDataRole.UserRole, {
                    "system_name": sys_name,
                    "node_index": node.index,
                    "node_name": node.name,
                    "peak_mw": peak,
                })
                # Disable nodes without buses (nothing to distribute)
                if len(buses) < 2:
                    node_item.setFlags(node_item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                    node_item.setToolTip(0, tr("wizard_demand.need_two_buses"))

        self._tree.expandAll()
        self._tree.blockSignals(False)
        self._update_summary()

    def _on_item_changed(self, item, column):
        self._update_summary()

    def _update_summary(self):
        targets = self.get_selected_targets()
        if not targets:
            self._summary_label.setText(tr("wizard_demand.no_selection"))
            return
        lines = []
        for t in targets:
            n_buses = len(t["buses"])
            lines.append(
                f"  {t['system_name']} / {t['node_name']}: "
                f"{t['peak_mw']:.1f} MW, {n_buses} buses"
            )
        self._summary_label.setText("\n".join(lines))

    def get_selected_targets(self) -> list[dict]:
        """Return list of checked nodes with their bus info.

        Each dict: {system_name, node_index, node_name, peak_mw, buses: list[GuiBus]}
        """
        targets = []
        root = self._tree.invisibleRootItem()
        for si in range(root.childCount()):
            sys_item = root.child(si)
            for ni in range(sys_item.childCount()):
                node_item = sys_item.child(ni)
                if node_item.checkState(0) == Qt.CheckState.Checked:
                    data = node_item.data(0, Qt.ItemDataRole.UserRole)
                    if data is None:
                        continue
                    sys_name = data["system_name"]
                    node_idx = data["node_index"]
                    state = self._all_states[sys_name]
                    # Distribute demand only across load/mixed buses
                    buses = _load_buses_of_node(state, node_idx)
                    targets.append({
                        "system_name": sys_name,
                        "node_index": node_idx,
                        "node_name": data["node_name"],
                        "peak_mw": data["peak_mw"],
                        "buses": buses,
                    })
        return targets

    def is_valid(self) -> bool:
        return len(self.get_selected_targets()) > 0


# =====================================================================
# Step 2: Domain & Fetch Buildings
# =====================================================================


class DomainFetchStep(QWidget):
    """Define the geographic domain and fetch building footprints."""

    buildingsReady = Signal()

    def __init__(self, map_widget, parent=None, geo_assets_provider=None):
        super().__init__(parent)
        self._map_widget = map_widget
        self._geo_assets_provider = geo_assets_provider
        self._bounds: Optional[tuple[float, float, float, float]] = None
        self._polygon: list[tuple[float, float]] = []
        self._buildings_gdf = None
        self._fetcher = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("wizard_demand.step2_instruction")))

        # Draw on map
        draw_group = QGroupBox(tr("wizard_common.draw_on_map"))
        draw_lay = QVBoxLayout(draw_group)
        self._btn_draw = QPushButton(tr("wizard_common.draw_rect"))
        self._btn_draw.clicked.connect(self._start_drawing)
        draw_lay.addWidget(self._btn_draw)
        self._draw_status = QLabel("")
        draw_lay.addWidget(self._draw_status)
        layout.addWidget(draw_group)

        from esfex.visualization.workflows._domain_geoasset_control import (
            GeoAssetDomainControl,
        )
        self._geo_domain_ctl = GeoAssetDomainControl(self._geo_assets_provider)
        self._geo_domain_ctl.domainPicked.connect(self._apply_domain_polygon)
        layout.addWidget(self._geo_domain_ctl)

        # Manual coordinates
        manual_group = QGroupBox(tr("wizard_common.manual_coords"))
        form = QFormLayout(manual_group)
        self._spin_south = self._coord_spin(-90, 90)
        self._spin_north = self._coord_spin(-90, 90)
        self._spin_west = self._coord_spin(-180, 180)
        self._spin_east = self._coord_spin(-180, 180)
        form.addRow(tr("wizard_common.south_lat"), self._spin_south)
        form.addRow(tr("wizard_common.north_lat"), self._spin_north)
        form.addRow(tr("wizard_common.west_lng"), self._spin_west)
        form.addRow(tr("wizard_common.east_lng"), self._spin_east)

        btn_row = QHBoxLayout()
        self._btn_apply = QPushButton(tr("wizard_common.apply_coords"))
        self._btn_apply.clicked.connect(self._apply_manual)
        btn_row.addWidget(self._btn_apply)
        self._btn_show = QPushButton(tr("wizard_common.show_on_map"))
        self._btn_show.clicked.connect(self._show_on_map)
        self._btn_show.setEnabled(False)
        btn_row.addWidget(self._btn_show)
        form.addRow(btn_row)
        layout.addWidget(manual_group)

        # Building source + fetch
        fetch_group = QGroupBox(tr("wizard_demand.building_source"))
        fetch_lay = QVBoxLayout(fetch_group)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel(tr("wizard_demand.source")))
        self._combo_source = QComboBox()
        self._combo_source.addItem("Overture Maps", "overture")
        self._combo_source.addItem("Microsoft ML", "microsoft")
        self._combo_source.addItem("Google Open Buildings", "google")
        src_row.addWidget(self._combo_source, 1)
        fetch_lay.addLayout(src_row)

        btn_fetch_row = QHBoxLayout()
        self._btn_fetch = QPushButton(tr("wizard_demand.fetch_buildings"))
        self._btn_fetch.clicked.connect(self._fetch_buildings)
        btn_fetch_row.addWidget(self._btn_fetch)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        btn_fetch_row.addWidget(self._progress, 1)
        fetch_lay.addLayout(btn_fetch_row)

        self._status = QLabel("")
        fetch_lay.addWidget(self._status)
        layout.addWidget(fetch_group)

        # Area info
        self._area_label = QLabel("")
        self._area_label.setStyleSheet("font-weight: bold; padding: 8px;")
        layout.addWidget(self._area_label)

        layout.addStretch()

        # Connect bridge signal for rectangle draw
        bridge = self._map_widget.bridge
        bridge.rectangleDrawn.connect(self._on_rectangle_drawn)
        self._map_widget.install_draw_cancel_handler(self, self._btn_draw)

    def _coord_spin(self, min_val, max_val):
        spin = QDoubleSpinBox()
        spin.setRange(min_val, max_val)
        spin.setDecimals(6)
        spin.setSingleStep(0.01)
        return spin

    def _start_drawing(self):
        self._draw_status.setText(tr("wizard_demand.drawing_status"))
        self._btn_draw.setEnabled(False)
        wizard = self.window()
        if wizard:
            wizard.showMinimized()
        self._map_widget.enable_rectangle_draw()

    def _on_rectangle_drawn(self, bounds_json: str):
        data = json.loads(bounds_json)
        south, west = float(data["south"]), float(data["west"])
        north, east = float(data["north"]), float(data["east"])
        self._bounds = (south, west, north, east)
        self._polygon = []   # drawn rectangle is bbox-only
        self._spin_south.setValue(south)
        self._spin_north.setValue(north)
        self._spin_west.setValue(west)
        self._spin_east.setValue(east)
        self._draw_status.setText(
            f"S={south:.4f}  W={west:.4f}  N={north:.4f}  E={east:.4f}"
        )
        self._btn_draw.setEnabled(True)
        self._btn_show.setEnabled(True)
        self._update_area()
        self._show_on_map()
        self._map_widget.disable_rectangle_draw()
        wizard = self.window()
        if wizard:
            wizard.showNormal()
            wizard.raise_()
            wizard.activateWindow()

    def _apply_manual(self):
        s = self._spin_south.value()
        n = self._spin_north.value()
        w = self._spin_west.value()
        e = self._spin_east.value()
        if n <= s or e <= w:
            QMessageBox.warning(
                self,
                tr("wizard_common.invalid_domain_title"),
                tr("wizard_demand.invalid_domain_msg"),
            )
            return
        self._bounds = (s, w, n, e)
        self._polygon = []   # manual bbox
        self._btn_show.setEnabled(True)
        self._update_area()

    def _apply_domain_polygon(self, poly):
        """Domain from an imported GeoAsset polygon (bbox fetch + polygon clip)."""
        from esfex.visualization.workflows.geo_domain import domain_bounds

        self._polygon = list(poly)
        s, w, n, e = domain_bounds(self._polygon)
        self._bounds = (s, w, n, e)
        self._spin_south.setValue(s)
        self._spin_north.setValue(n)
        self._spin_west.setValue(w)
        self._spin_east.setValue(e)
        self._draw_status.setText(
            f"Domain polygon: {len(self._polygon)} vertices")
        self._btn_show.setEnabled(True)
        self._update_area()
        try:
            self._map_widget.show_domain_polygon(self._polygon)
        except Exception:
            self._show_on_map()

    def get_polygon(self) -> list[tuple[float, float]]:
        return self._polygon

    def _show_on_map(self):
        if self._bounds:
            s, w, n, e = self._bounds
            self._map_widget.show_demand_domain(s, w, n, e)
            self._map_widget.fit_bounds(s, w, n, e)

    def _update_area(self):
        if not self._bounds:
            return
        s, w, n, e = self._bounds
        lat_mid = (s + n) / 2.0
        lat_km = (n - s) * 111.32
        lon_km = (e - w) * 111.32 * math.cos(math.radians(lat_mid))
        area = lat_km * lon_km
        self._area_label.setText(tr("wizard_demand.approx_area", area=f"{area:.2f}"))

    def _fetch_buildings(self):
        if self._bounds is None:
            QMessageBox.warning(
                self,
                tr("wizard_common.no_domain_title"),
                tr("wizard_common.no_domain_msg"),
            )
            return

        from esfex.visualization.workflows.data_fetchers import BuildingFetcher

        source = self._combo_source.currentData()
        self._btn_fetch.setEnabled(False)
        self._status.setText(tr("wizard_demand.fetching"))
        self._progress.setValue(0)

        self._fetcher = BuildingFetcher(source, self._bounds)
        self._fetcher.progress.connect(self._on_progress)
        self._fetcher.finished.connect(self._on_finished)
        self._fetcher.error.connect(self._on_error)
        self._fetcher.start()

    def _on_progress(self, pct, msg):
        self._progress.setValue(pct)
        self._status.setText(msg)

    def _on_finished(self, gdf):
        # Clip fetched buildings to the precise domain polygon (if any).
        if self._polygon and len(self._polygon) >= 3 and gdf is not None and len(gdf) > 0:
            try:
                from esfex.visualization.workflows.geo_domain import (
                    domain_shapely,
                )
                gdf = gdf[gdf.geometry.intersects(domain_shapely(self._polygon))]
            except Exception:
                pass
        self._buildings_gdf = gdf
        self._btn_fetch.setEnabled(True)
        n = len(gdf) if gdf is not None else 0
        self._status.setText(tr("wizard_demand.buildings_loaded", count=n))
        self._progress.setValue(100)
        self.buildingsReady.emit()

    def _on_error(self, msg):
        self._btn_fetch.setEnabled(True)
        self._status.setText(f"Error: {msg}")
        self._progress.setValue(0)

    def get_buildings(self):
        return self._buildings_gdf

    def is_valid(self) -> bool:
        return self._buildings_gdf is not None and len(self._buildings_gdf) > 0


# =====================================================================
# Step 3: Building Classification
# =====================================================================


class ClassificationStep(QWidget):
    """Configure building type rules and preview classification weights."""

    classificationReady = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buildings_gdf = None
        self._classified_gdf = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("wizard_demand.step3_instruction")))

        # Rules table
        rules_group = QGroupBox(tr("wizard_demand.building_rules"))
        rules_lay = QVBoxLayout(rules_group)

        self._rules_table = QTableWidget(0, 5)
        self._rules_table.setHorizontalHeaderLabels([
            tr("wizard_demand.rule_name"),
            tr("wizard_demand.area_min"),
            tr("wizard_demand.area_max"),
            tr("wizard_demand.weight_density"),
            tr("wizard_demand.rule_color"),
        ])
        self._rules_table.horizontalHeader().setStretchLastSection(True)
        rules_lay.addWidget(self._rules_table)

        btn_row = QHBoxLayout()
        self._btn_add_rule = QPushButton(tr("wizard_demand.add_rule"))
        self._btn_add_rule.clicked.connect(self._add_empty_rule)
        btn_row.addWidget(self._btn_add_rule)
        self._btn_remove_rule = QPushButton(tr("wizard_demand.remove_rule"))
        self._btn_remove_rule.clicked.connect(self._remove_selected_rule)
        btn_row.addWidget(self._btn_remove_rule)
        btn_row.addStretch()
        rules_lay.addLayout(btn_row)
        layout.addWidget(rules_group)

        # Fallback weight
        fallback_row = QHBoxLayout()
        fallback_row.addWidget(QLabel(tr("wizard_demand.fallback_weight")))
        self._spin_fallback = QDoubleSpinBox()
        self._spin_fallback.setRange(0.0, 1.0)
        self._spin_fallback.setDecimals(4)
        self._spin_fallback.setSingleStep(0.01)
        self._spin_fallback.setValue(0.03)
        fallback_row.addWidget(self._spin_fallback)
        fallback_row.addStretch()
        layout.addLayout(fallback_row)

        # Classify button + preview
        classify_row = QHBoxLayout()
        self._btn_classify = QPushButton(tr("wizard_demand.classify"))
        self._btn_classify.clicked.connect(self._run_classification)
        classify_row.addWidget(self._btn_classify)
        classify_row.addStretch()
        layout.addLayout(classify_row)

        self._preview_label = QLabel("")
        self._preview_label.setStyleSheet("padding: 8px;")
        self._preview_label.setWordWrap(True)
        layout.addWidget(self._preview_label)

        # Weight info note
        self._info_label = QLabel(tr("wizard_demand.weight_info"))
        self._info_label.setWordWrap(True)
        self._info_label.setStyleSheet("color: #888; padding: 4px; font-style: italic;")
        layout.addWidget(self._info_label)

        layout.addStretch()

        # Populate default rules
        self._load_rules(DEFAULT_RULES)

    def _load_rules(self, rules: list[BuildingTypeRule]):
        self._rules_table.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            self._rules_table.setItem(row, 0, QTableWidgetItem(rule.name))
            self._rules_table.setItem(row, 1, QTableWidgetItem(str(rule.area_min_m2)))
            area_max_str = "∞" if rule.area_max_m2 == math.inf else str(rule.area_max_m2)
            self._rules_table.setItem(row, 2, QTableWidgetItem(area_max_str))
            self._rules_table.setItem(row, 3, QTableWidgetItem(str(rule.weight_per_m2)))
            self._rules_table.setItem(row, 4, QTableWidgetItem(rule.color))

    def _get_rules(self) -> list[BuildingTypeRule]:
        rules = []
        for row in range(self._rules_table.rowCount()):
            name = self._rules_table.item(row, 0)
            area_min = self._rules_table.item(row, 1)
            area_max = self._rules_table.item(row, 2)
            weight = self._rules_table.item(row, 3)
            color = self._rules_table.item(row, 4)
            if name is None:
                continue
            area_max_val = math.inf
            if area_max and area_max.text() not in ("∞", "inf", ""):
                try:
                    area_max_val = float(area_max.text())
                except ValueError:
                    area_max_val = math.inf
            rules.append(BuildingTypeRule(
                name=name.text(),
                area_min_m2=float(area_min.text()) if area_min else 0.0,
                area_max_m2=area_max_val,
                weight_per_m2=float(weight.text()) if weight else 0.05,
                color=color.text() if color else "#3498db",
            ))
        return rules

    def _add_empty_rule(self):
        row = self._rules_table.rowCount()
        self._rules_table.insertRow(row)
        self._rules_table.setItem(row, 0, QTableWidgetItem("New Type"))
        self._rules_table.setItem(row, 1, QTableWidgetItem("0"))
        self._rules_table.setItem(row, 2, QTableWidgetItem("∞"))
        self._rules_table.setItem(row, 3, QTableWidgetItem("0.05"))
        self._rules_table.setItem(row, 4, QTableWidgetItem("#3498db"))

    def _remove_selected_rule(self):
        row = self._rules_table.currentRow()
        if row >= 0:
            self._rules_table.removeRow(row)

    def set_buildings(self, gdf):
        self._buildings_gdf = gdf
        self._classified_gdf = None

    def _run_classification(self):
        if self._buildings_gdf is None or self._buildings_gdf.empty:
            self._preview_label.setText(tr("wizard_demand.no_buildings"))
            return

        rules = self._get_rules()
        fallback = self._spin_fallback.value()
        self._classified_gdf = classify_buildings(self._buildings_gdf, rules, fallback)

        summary = compute_classification_summary(self._classified_gdf)
        lines = []
        for _, row in summary.iterrows():
            lines.append(
                f"  {row['building_type']}: {int(row['count'])} buildings, "
                f"{row['total_area_m2']:.0f} m², weight={row['total_weight']:.1f}"
            )
        total_w = summary["total_weight"].sum()
        lines.append(f"\n  {tr('wizard_demand.total_weight')}: {total_w:.1f}")
        self._preview_label.setText("\n".join(lines))
        self.classificationReady.emit()

    def get_classified_buildings(self):
        return self._classified_gdf

    def is_valid(self) -> bool:
        return self._classified_gdf is not None and not self._classified_gdf.empty


# =====================================================================
# Step 4: Spatial Clustering
# =====================================================================


class ClusteringStep(QWidget):
    """Configure and run spatial clustering."""

    clusteringReady = Signal()

    def __init__(self, map_widget, parent=None):
        super().__init__(parent)
        self._map_widget = map_widget
        self._classified_gdf = None
        self._clustered_gdf = None
        self._cluster_summary = None
        self._worker = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("wizard_demand.step4_instruction")))

        # Algorithm selector
        algo_group = QGroupBox(tr("wizard_demand.algorithm"))
        algo_lay = QVBoxLayout(algo_group)

        algo_row = QHBoxLayout()
        algo_row.addWidget(QLabel(tr("wizard_demand.algorithm_label")))
        self._algo_combo = QComboBox()
        self._algo_combo.addItem("DBSCAN", "dbscan")
        self._algo_combo.addItem("KMeans", "kmeans")
        self._algo_combo.addItem(tr("wizard_demand.agglomerative"), "agglomerative")
        self._algo_combo.currentIndexChanged.connect(self._on_algo_changed)
        algo_row.addWidget(self._algo_combo, 1)
        algo_lay.addLayout(algo_row)

        # DBSCAN parameters
        self._dbscan_widget = QWidget()
        dbscan_form = QFormLayout(self._dbscan_widget)
        self._spin_eps = QSpinBox()
        self._spin_eps.setRange(50, 5000)
        self._spin_eps.setValue(500)
        self._spin_eps.setSuffix(" m")
        dbscan_form.addRow(tr("wizard_demand.eps"), self._spin_eps)
        self._spin_min_samples = QSpinBox()
        self._spin_min_samples.setRange(1, 100)
        self._spin_min_samples.setValue(5)
        dbscan_form.addRow(tr("wizard_demand.min_samples"), self._spin_min_samples)
        algo_lay.addWidget(self._dbscan_widget)

        # KMeans parameters
        self._kmeans_widget = QWidget()
        kmeans_form = QFormLayout(self._kmeans_widget)
        self._spin_k = QSpinBox()
        self._spin_k.setRange(2, 50)
        self._spin_k.setValue(3)
        kmeans_form.addRow(tr("wizard_demand.n_clusters"), self._spin_k)
        algo_lay.addWidget(self._kmeans_widget)
        self._kmeans_widget.hide()

        # Agglomerative parameters
        self._agg_widget = QWidget()
        agg_form = QFormLayout(self._agg_widget)
        self._spin_agg_k = QSpinBox()
        self._spin_agg_k.setRange(2, 50)
        self._spin_agg_k.setValue(3)
        agg_form.addRow(tr("wizard_demand.n_clusters"), self._spin_agg_k)
        self._combo_linkage = QComboBox()
        self._combo_linkage.addItems(["ward", "complete", "average"])
        agg_form.addRow(tr("wizard_demand.linkage"), self._combo_linkage)
        algo_lay.addWidget(self._agg_widget)
        self._agg_widget.hide()

        layout.addWidget(algo_group)

        # Run button + progress
        run_row = QHBoxLayout()
        self._btn_run = QPushButton(tr("wizard_demand.run_clustering"))
        self._btn_run.clicked.connect(self._run_clustering)
        run_row.addWidget(self._btn_run)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        run_row.addWidget(self._progress, 1)
        layout.addLayout(run_row)

        self._status = QLabel("")
        layout.addWidget(self._status)

        # Results table
        self._result_table = QTableWidget(0, 5)
        self._result_table.setHorizontalHeaderLabels([
            tr("wizard_demand.cluster_id"),
            tr("wizard_demand.building_count"),
            tr("wizard_demand.total_weight"),
            tr("wizard_demand.demand_fraction"),
            tr("wizard_demand.cluster_color"),
        ])
        self._result_table.horizontalHeader().setStretchLastSection(True)
        self._result_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._result_table.setMinimumHeight(280)  # ~10 visible rows
        layout.addWidget(self._result_table, 1)

    def _on_algo_changed(self, _idx):
        algo = self._algo_combo.currentData()
        self._dbscan_widget.setVisible(algo == "dbscan")
        self._kmeans_widget.setVisible(algo == "kmeans")
        self._agg_widget.setVisible(algo == "agglomerative")

    def set_classified_buildings(self, gdf):
        self._classified_gdf = gdf
        self._clustered_gdf = None
        self._cluster_summary = None
        self._result_table.setRowCount(0)

    def set_default_clusters(self, n: int):
        """Set default cluster count (e.g. from max bus count)."""
        self._spin_k.setValue(max(2, n))
        self._spin_agg_k.setValue(max(2, n))

    def _get_params(self) -> dict:
        algo = self._algo_combo.currentData()
        if algo == "dbscan":
            return {"eps": self._spin_eps.value(), "min_samples": self._spin_min_samples.value()}
        elif algo == "kmeans":
            return {"n_clusters": self._spin_k.value()}
        elif algo == "agglomerative":
            return {"n_clusters": self._spin_agg_k.value(), "linkage": self._combo_linkage.currentText()}
        return {}

    def _run_clustering(self):
        if self._classified_gdf is None or self._classified_gdf.empty:
            self._status.setText(tr("wizard_demand.no_classified"))
            return

        algo = self._algo_combo.currentData()
        params = self._get_params()

        self._btn_run.setEnabled(False)
        self._progress.setValue(0)
        self._status.setText(tr("wizard_demand.clustering_running"))

        self._worker = ClusteringWorker(self._classified_gdf, algo, params)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, pct, msg):
        self._progress.setValue(pct)
        self._status.setText(msg)

    def _on_finished(self, clustered_gdf, summary_df):
        self._clustered_gdf = clustered_gdf
        self._cluster_summary = summary_df
        self._btn_run.setEnabled(True)
        self._progress.setValue(100)

        n_clusters = len(summary_df)
        self._status.setText(tr("wizard_demand.clustering_done", n=n_clusters))

        # Populate results table
        self._result_table.setRowCount(len(summary_df))
        for row, (_, r) in enumerate(summary_df.iterrows()):
            self._result_table.setItem(row, 0, QTableWidgetItem(str(int(r["cluster_id"]))))
            self._result_table.setItem(row, 1, QTableWidgetItem(str(int(r["count"]))))
            self._result_table.setItem(row, 2, QTableWidgetItem(f"{r['total_weight']:.1f}"))
            self._result_table.setItem(row, 3, QTableWidgetItem(f"{r['demand_fraction']:.4f}"))
            color_item = QTableWidgetItem(r["color"])
            color_item.setBackground(self._parse_color(r["color"]))
            self._result_table.setItem(row, 4, color_item)

        # Show clusters on map
        self._show_clusters_on_map()
        self.clusteringReady.emit()

    def _on_error(self, msg):
        self._btn_run.setEnabled(True)
        self._status.setText(f"Error: {msg}")
        self._progress.setValue(0)

    def _show_clusters_on_map(self):
        if self._clustered_gdf is None:
            return
        points = []
        gdf = self._clustered_gdf
        for _, row in gdf.iterrows():
            centroid = row.geometry.centroid
            cid = int(row["cluster_id"])
            points.append({
                "lat": centroid.y,
                "lng": centroid.x,
                "cluster_id": cid,
                "color": CLUSTER_COLORS[cid % len(CLUSTER_COLORS)],
            })
        self._map_widget.show_demand_clusters(points)

    @staticmethod
    def _parse_color(hex_color: str):
        from PySide6.QtGui import QColor
        return QColor(hex_color)

    def get_clustered_buildings(self):
        return self._clustered_gdf

    def get_cluster_summary(self):
        return self._cluster_summary

    def is_valid(self) -> bool:
        return self._cluster_summary is not None and len(self._cluster_summary) > 0


# =====================================================================
# Step 5: Review & Apply — per-node bus ↔ cluster mapping + apply
# =====================================================================


class ReviewApplyStep(QWidget):
    """Review per-node mapping of clusters to buses and apply changes.

    Fractions are computed by the algorithm and are read-only.
    Includes apply and export functionality (merged from former Step 6).
    """

    applied = Signal()

    def __init__(self, model, all_states: dict, current_system_name: str, parent=None):
        super().__init__(parent)
        self._model = model
        self._all_states = all_states
        self._current_system_name = current_system_name
        self._assignments: list[dict] = []
        self._applied = False

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("wizard_demand.step5_instruction")))

        # Per-node assignment table
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels([
            tr("wizard_demand.system_label"),
            tr("wizard_demand.node_label"),
            tr("wizard_demand.bus_id"),
            tr("wizard_demand.bus_name"),
            tr("wizard_demand.cluster_name"),
            tr("wizard_demand.old_fraction"),
            tr("wizard_demand.new_fraction"),
        ])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setMinimumHeight(280)  # ~10 visible rows
        layout.addWidget(self._table, 1)

        # Sum verification per node
        self._sum_label = QLabel("")
        self._sum_label.setStyleSheet("padding: 8px; color: #27ae60; font-weight: bold;")
        layout.addWidget(self._sum_label)

        # Apply + Export buttons
        btn_row = QHBoxLayout()
        self._btn_apply = QPushButton(tr("wizard_demand.apply"))
        self._btn_apply.setStyleSheet(
            "font-weight: bold; padding: 8px 24px; font-size: 14px;"
        )
        self._btn_apply.clicked.connect(self._apply)
        btn_row.addWidget(self._btn_apply)
        btn_row.addStretch()

        self._btn_export_csv = QPushButton(tr("wizard_demand.export_csv"))
        self._btn_export_csv.clicked.connect(self._export_csv)
        self._btn_export_csv.setEnabled(False)
        btn_row.addWidget(self._btn_export_csv)
        layout.addLayout(btn_row)

        self._result_label = QLabel("")
        self._result_label.setStyleSheet("font-weight: bold; padding: 8px; color: #27ae60;")
        layout.addWidget(self._result_label)

    def set_data(self, targets: list[dict], cluster_summary):
        """Build the bus ↔ cluster assignment mapping.

        For each target node, sort clusters by demand_fraction descending
        and map them to that node's buses sorted by bus_id.
        """
        self._assignments = []
        self._applied = False
        self._btn_apply.setEnabled(True)
        self._btn_export_csv.setEnabled(False)
        self._result_label.setText("")

        if cluster_summary is None or cluster_summary.empty:
            return

        # Sort clusters by demand_fraction descending
        sorted_clusters = cluster_summary.sort_values(
            "demand_fraction", ascending=False
        ).reset_index(drop=True)

        rows = []
        for target in targets:
            sys_name = target["system_name"]
            node_name = target["node_name"]
            buses = sorted(target["buses"], key=lambda b: b.bus_id)
            n_buses = len(buses)

            if n_buses == 0:
                continue

            # Distribute cluster fractions among this node's buses.
            # If more clusters than buses: aggregate tail clusters into last bus.
            # If fewer clusters than buses: remaining get 0.
            fractions = sorted_clusters["demand_fraction"].tolist()

            bus_fractions = []
            if n_buses >= len(fractions):
                bus_fractions = fractions[:] + [0.0] * (n_buses - len(fractions))
            else:
                bus_fractions = fractions[:n_buses - 1]
                bus_fractions.append(sum(fractions[n_buses - 1:]))

            # Normalize to exactly 1.0
            frac_sum = sum(bus_fractions)
            if frac_sum > 0:
                bus_fractions = [f / frac_sum for f in bus_fractions]

            for i, bus in enumerate(buses):
                frac = bus_fractions[i] if i < len(bus_fractions) else 0.0
                cluster_name = (
                    f"Cluster {int(sorted_clusters.iloc[i]['cluster_id'])}"
                    if i < len(sorted_clusters) else "—"
                )
                assignment = {
                    "system_name": sys_name,
                    "node_name": node_name,
                    "node_index": target["node_index"],
                    "bus_id": bus.bus_id,
                    "bus_name": bus.name,
                    "old_fraction": bus.demand_fraction,
                    "new_fraction": frac,
                    "cluster_name": cluster_name,
                }
                self._assignments.append(assignment)
                rows.append(assignment)

        # Populate table
        self._table.setRowCount(len(rows))
        for row_idx, a in enumerate(rows):
            self._table.setItem(row_idx, 0, QTableWidgetItem(a["system_name"]))
            self._table.setItem(row_idx, 1, QTableWidgetItem(a["node_name"]))
            self._table.setItem(row_idx, 2, QTableWidgetItem(a["bus_id"]))
            self._table.setItem(row_idx, 3, QTableWidgetItem(a["bus_name"]))
            self._table.setItem(row_idx, 4, QTableWidgetItem(a["cluster_name"]))
            self._table.setItem(row_idx, 5, QTableWidgetItem(f"{a['old_fraction']:.4f}"))
            self._table.setItem(row_idx, 6, QTableWidgetItem(f"{a['new_fraction']:.4f}"))

        # Show per-node sum verification
        node_sums = {}
        for a in rows:
            key = f"{a['system_name']}/{a['node_name']}"
            node_sums[key] = node_sums.get(key, 0.0) + a["new_fraction"]
        lines = [f"  {k}: \u03a3 = {v:.4f}" for k, v in node_sums.items()]
        self._sum_label.setText("\n".join(lines))

    def _apply(self):
        if not self._assignments:
            return

        skipped_connection = 0
        for a in self._assignments:
            sys_name = a["system_name"]
            bus_id = a["bus_id"]
            new_frac = a["new_fraction"]

            # Defensive: never write demand to a connection bus
            state = self._all_states.get(sys_name)
            target_bus = state.buses.get(bus_id) if state else None
            if target_bus is not None and target_bus.role == "connection":
                skipped_connection += 1
                continue

            if sys_name == self._current_system_name:
                self._model.update_bus(bus_id, demand_fraction=new_frac)
            elif state and bus_id in state.buses:
                state.buses[bus_id].demand_fraction = new_frac

        if skipped_connection:
            import logging
            logging.getLogger(__name__).warning(
                "Skipped %d connection bus(es) during demand distribution",
                skipped_connection,
            )

        self._applied = True
        self._btn_apply.setEnabled(False)
        self._btn_export_csv.setEnabled(True)
        self._result_label.setText(tr("wizard_demand.applied_success"))
        self.applied.emit()

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("wizard_demand.export_csv_title"),
            "demand_distribution.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        import csv
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["system_name", "node_name", "bus_id", "bus_name",
                             "old_fraction", "new_fraction"],
            )
            writer.writeheader()
            for a in self._assignments:
                writer.writerow({
                    "system_name": a["system_name"],
                    "node_name": a["node_name"],
                    "bus_id": a["bus_id"],
                    "bus_name": a["bus_name"],
                    "old_fraction": f"{a['old_fraction']:.4f}",
                    "new_fraction": f"{a['new_fraction']:.4f}",
                })
        self._result_label.setText(tr("wizard_demand.exported", path=path))

    def get_assignments(self) -> list[dict]:
        return self._assignments

    def is_valid(self) -> bool:
        return self._applied
