"""Dialog for assigning GeoJSON features to system element types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from esfex.visualization.i18n import tr
from esfex.visualization.panels.word_wrap_header import WordWrapHeaderView


@dataclass
class ParseAssignment:
    """A single feature-to-element assignment."""

    feature_index: int
    geometry_type: str  # Point, LineString, Polygon
    target_type: str  # node, generator, battery, line, zone, ...
    properties: dict
    coordinates: list
    target_node: int | None = None  # user override; None = auto (nearest centroid)


# Geometry type -> list of (target_type, display_label)
_TARGETS_BY_GEOMETRY = {
    "Point": [
        ("generator", "Generator"),
        ("battery", "Battery"),
        ("fuel_entry", "Fuel Entry"),
        ("electrolyzer", "Electrolyzer"),
        ("transformer", "Transformer"),
        ("bus", "Bus"),
        ("acdc_converter", "AC/DC Converter"),
        ("freq_converter", "Freq. Converter"),
        ("fuel_storage", "Fuel Storage"),
        ("skip", "Skip"),
    ],
    "LineString": [
        ("line", "Transmission Line"),
        ("fuel_route", "Fuel Transport Route"),
        ("skip", "Skip"),
    ],
    "MultiLineString": [
        ("line", "Transmission Line"),
        ("fuel_route", "Fuel Transport Route"),
        ("skip", "Skip"),
    ],
    "Polygon": [
        ("zone", "Development Zone"),
        ("skip", "Skip"),
    ],
    "MultiPolygon": [
        ("zone", "Development Zone"),
        ("skip", "Skip"),
    ],
    "MultiPoint": [
        ("skip", "Skip"),
    ],
}

# Default target for each geometry type
_DEFAULT_TARGET = {
    "Point": "generator",
    "LineString": "line",
    "MultiLineString": "line",
    "Polygon": "zone",
    "MultiPolygon": "zone",
    "MultiPoint": "skip",
}

# Geometry types where per-feature node selection makes no sense
# (endpoints are resolved independently by snapping)
_LINE_GEOM_TYPES = {"LineString", "MultiLineString"}
_POLYGON_GEOM_TYPES = {"Polygon", "MultiPolygon"}


class ParseGeoAssetDialog(QDialog):
    """Dialog to assign imported geo features to system element types."""

    def __init__(
        self,
        geojson_data: dict,
        asset_name: str,
        system_names: list[str] | None = None,
        default_system: str = "",
        nodes: list | None = None,
        on_system_changed: Callable[[str], list] | None = None,
        parent=None,
    ):
        """
        Parameters
        ----------
        system_names : list of system names (omit or None to hide system combo)
        default_system : pre-selected system name
        nodes : list[GuiNode] of the default system (for node column)
        on_system_changed : callback(system_name) -> list[GuiNode], called when
            user changes the system combo; returns the new system's nodes
        """
        super().__init__(parent)
        self.setWindowTitle(tr("geo_parse.title", name=asset_name))
        self.setMinimumSize(800, 500)

        self._features = self._extract_features(geojson_data)
        self._combos: list[QComboBox] = []
        self._node_combos: list[QComboBox] = []
        # Explicit target list per row (avoids relying on Qt userData)
        self._row_targets: list[list[str]] = []
        self._nodes: list = nodes or []
        self._on_system_changed = on_system_changed

        layout = QVBoxLayout(self)

        # Header
        header = QLabel(
            f"<b>{asset_name}</b> — {len(self._features)} feature(s)"
        )
        layout.addWidget(header)

        # System selector (only if multiple systems)
        self._system_combo: QComboBox | None = None
        self._single_system = default_system
        if system_names and len(system_names) > 1:
            sys_row = QHBoxLayout()
            sys_row.addWidget(QLabel(tr("geo_parse.target_system")))
            self._system_combo = QComboBox()
            for name in system_names:
                self._system_combo.addItem(name)
            if default_system in system_names:
                self._system_combo.setCurrentIndex(system_names.index(default_system))
            self._system_combo.currentTextChanged.connect(self._on_system_combo_changed)
            sys_row.addWidget(self._system_combo)
            sys_row.addStretch()
            layout.addLayout(sys_row)

        # Quick-assign row
        quick_row = QHBoxLayout()
        quick_row.addWidget(QLabel(tr("geo_parse.assign_all")))
        for geom_type in ("Point", "LineString", "Polygon"):
            targets = _TARGETS_BY_GEOMETRY.get(geom_type, [])
            if not targets:
                continue
            count = sum(1 for f in self._features if f["geom_type"] == geom_type)
            if count == 0:
                continue
            combo = QComboBox()
            combo.addItem(f"\u2014 {geom_type}s ({count}) \u2014")
            target_keys = []
            for ttype, tlabel in targets:
                combo.addItem(tlabel)
                target_keys.append(ttype)
            combo.currentIndexChanged.connect(
                lambda idx, keys=target_keys, g=geom_type: self._on_quick_assign(
                    idx, keys, g
                )
            )
            quick_row.addWidget(combo)
        quick_row.addStretch()
        layout.addLayout(quick_row)

        # Feature table (5 columns: #, Type, Name, Target, Node)
        self._table = QTableWidget(len(self._features), 5)
        self._table.setHorizontalHeader(WordWrapHeaderView(self._table))
        self._table.setHorizontalHeaderLabels([
            tr("geo_parse.col_num"),
            tr("geo_parse.col_type"),
            tr("geo_parse.col_name"),
            tr("geo_parse.col_target"),
            tr("geo_parse.col_node"),
        ])
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)

        for row, feat in enumerate(self._features):
            geom_type = feat["geom_type"]
            name = feat["name"]

            # # column
            num_item = QTableWidgetItem(str(row + 1))
            num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 0, num_item)

            # Geometry column
            geom_item = QTableWidgetItem(geom_type)
            self._table.setItem(row, 1, geom_item)

            # Name column
            name_item = QTableWidgetItem(name)
            self._table.setItem(row, 2, name_item)

            # Assign To combo — use plain text items, track targets in parallel list
            combo = QComboBox()
            targets = _TARGETS_BY_GEOMETRY.get(geom_type, [("skip", "Skip")])
            default_target = _DEFAULT_TARGET.get(geom_type, "skip")
            default_idx = 0
            target_keys = []
            for i, (ttype, tlabel) in enumerate(targets):
                combo.addItem(tlabel)
                target_keys.append(ttype)
                if ttype == default_target:
                    default_idx = i
            combo.setCurrentIndex(default_idx)
            self._table.setCellWidget(row, 3, combo)
            self._combos.append(combo)
            self._row_targets.append(target_keys)

            # Node combo
            node_combo = QComboBox()
            self._populate_node_combo(node_combo)
            # Disable for lines/polygons (endpoints resolve independently)
            if geom_type in _LINE_GEOM_TYPES or geom_type in _POLYGON_GEOM_TYPES:
                node_combo.setEnabled(False)
            self._table.setCellWidget(row, 4, node_combo)
            self._node_combos.append(node_combo)

        self._table.setColumnWidth(0, 40)
        self._table.setColumnWidth(1, 100)
        self._table.setColumnWidth(3, 160)
        self._table.setColumnWidth(4, 150)
        layout.addWidget(self._table)

        # Snap threshold
        snap_row = QHBoxLayout()
        snap_row.addWidget(QLabel(tr("geo_parse.snap_threshold")))
        self._snap_spin = QDoubleSpinBox()
        self._snap_spin.setRange(0.1, 100.0)
        self._snap_spin.setValue(5.0)
        self._snap_spin.setDecimals(1)
        snap_row.addWidget(self._snap_spin)
        snap_row.addStretch()
        layout.addLayout(snap_row)

        # OK / Cancel
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton(tr("geo_parse.cancel_btn"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton(tr("geo_parse.parse_btn"))
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Public getters
    # ------------------------------------------------------------------

    def get_assignments(self) -> list[ParseAssignment]:
        """Return assignments for all non-skipped features."""
        assignments = []
        for row, feat in enumerate(self._features):
            combo = self._combos[row]
            idx = combo.currentIndex()
            targets = self._row_targets[row]
            if idx < 0 or idx >= len(targets):
                continue
            target = targets[idx]
            if target == "skip":
                continue

            # Node override
            node_combo = self._node_combos[row]
            target_node: int | None = None
            if node_combo.isEnabled() and node_combo.currentIndex() > 0:
                # index 0 = "Auto (nearest)", 1+ = actual nodes
                node_data = node_combo.currentData()
                if node_data is not None:
                    target_node = int(node_data)

            assignments.append(ParseAssignment(
                feature_index=row,
                geometry_type=feat["geom_type"],
                target_type=target,
                properties=feat["properties"],
                coordinates=feat["coordinates"],
                target_node=target_node,
            ))
        return assignments

    def get_snap_threshold(self) -> float:
        return self._snap_spin.value()

    def get_target_system(self) -> str:
        if self._system_combo:
            return self._system_combo.currentText()
        return self._single_system

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _populate_node_combo(self, combo: QComboBox):
        """Fill a node combo with Auto + available nodes."""
        combo.clear()
        combo.addItem(tr("geo_parse.auto_nearest"), None)
        for node in self._nodes:
            label = f"Node {node.index}"
            if hasattr(node, "name") and node.name:
                label += f" - {node.name}"
            combo.addItem(label, node.index)

    def _refresh_all_node_combos(self):
        """Rebuild all node combo boxes after system change."""
        for row, combo in enumerate(self._node_combos):
            prev_data = combo.currentData()
            self._populate_node_combo(combo)
            # Try to restore previous selection
            if prev_data is not None:
                for i in range(combo.count()):
                    if combo.itemData(i) == prev_data:
                        combo.setCurrentIndex(i)
                        break

    def _on_system_combo_changed(self, system_name: str):
        """User changed the target system — refresh node list."""
        if self._on_system_changed:
            self._nodes = self._on_system_changed(system_name)
            self._refresh_all_node_combos()

    @staticmethod
    def _extract_features(geojson: dict) -> list[dict]:
        """Extract a flat list of feature info dicts from GeoJSON."""
        features_raw = geojson.get("features", [])
        if not features_raw and geojson.get("type") == "Feature":
            features_raw = [geojson]
        elif not features_raw and geojson.get("geometry"):
            features_raw = [geojson]

        result = []
        for feat in features_raw:
            geom = feat.get("geometry") or {}
            props = feat.get("properties") or {}
            geom_type = geom.get("type", "Unknown")
            coords = geom.get("coordinates", [])
            name = (
                props.get("name")
                or props.get("Name")
                or props.get("NAME")
                or props.get("label")
                or f"Feature {len(result) + 1}"
            )
            result.append({
                "geom_type": geom_type,
                "name": str(name),
                "properties": props,
                "coordinates": coords,
            })
        return result

    def _on_quick_assign(self, idx: int, keys: list[str], geom_type: str):
        """Bulk-assign all features of a geometry type."""
        # idx 0 is the header "— Points (N) —", actual targets start at 1
        if idx <= 0:
            return
        target = keys[idx - 1]  # offset by 1 for header
        for row, feat in enumerate(self._features):
            if feat["geom_type"] == geom_type:
                row_targets = self._row_targets[row]
                try:
                    target_idx = row_targets.index(target)
                    self._combos[row].setCurrentIndex(target_idx)
                except ValueError:
                    pass
