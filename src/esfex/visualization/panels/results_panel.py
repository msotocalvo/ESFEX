"""Floating results overlay panel for the map."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.i18n import tr
from esfex.visualization.panels.results_charts import (
    _DATASET_T,
    _GROUP_T,
    _open_scenario,
    _open_summary_results,
    _open_system_config,
    _system_node_range,
)

if TYPE_CHECKING:
    from esfex.visualization.map_widget import MapWidget


class _CheckableComboBox(QComboBox):
    """A QComboBox whose dropdown items carry checkboxes for multi-select.

    The closed combo shows a summary ("3 selected"); clicking an item
    toggles its check without closing the popup. Emits
    ``selectionChanged`` whenever the checked set changes. A per-item
    ``UserRole`` payload (here the variable's viz_type) lets callers
    enforce rules — e.g. mutually-exclusive flow layers.
    """

    selectionChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModel(QStandardItemModel(self))
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setPlaceholderText(tr("results_panel.variable"))
        # Toggle on click instead of selecting+closing.
        self.view().pressed.connect(self._on_item_pressed)
        # Optional hook the panel sets to apply exclusion rules when an
        # item is checked: fn(model, checked_item) -> None.
        self.on_item_checked = None

    def add_checkable_item(self, text: str, data=None):
        item = QStandardItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        item.setData(Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        if data is not None:
            item.setData(data, Qt.ItemDataRole.UserRole)
        self.model().appendRow(item)

    def _on_item_pressed(self, index):
        item = self.model().itemFromIndex(index)
        if item is None:
            return
        checked = item.checkState() == Qt.CheckState.Checked
        item.setCheckState(
            Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked
        )
        if callable(self.on_item_checked) and not checked:
            # Only run exclusion logic when an item was just *checked*.
            self.on_item_checked(self.model(), item)
        self._refresh_text()
        self.selectionChanged.emit()

    def checked_rows(self) -> list[int]:
        m = self.model()
        return [r for r in range(m.rowCount())
                if m.item(r).checkState() == Qt.CheckState.Checked]

    def set_checked_rows(self, rows):
        m = self.model()
        want = set(rows)
        for r in range(m.rowCount()):
            m.item(r).setCheckState(
                Qt.CheckState.Checked if r in want else Qt.CheckState.Unchecked
            )
        self._refresh_text()

    def _refresh_text(self):
        rows = self.checked_rows()
        m = self.model()
        if not rows:
            self.lineEdit().setText("")
        elif len(rows) == 1:
            self.lineEdit().setText(m.item(rows[0]).text())
        else:
            self.lineEdit().setText(f"{len(rows)} selected")

# ── Variable registry ─────────────────────────────────────────────

_NODE_VARIABLES = [
    # (display_name, hdf5_key, aggregation, viz_type)
    ("Installed Capacity Mix", "generation", "installed_capacity", "pie_chart"),
    ("Generation Mix", "generation", "mix_by_gen", "pie_chart"),
    ("RE vs Non-RE", "generation", "re_nonre", "pie_chart"),
    ("Total Generation", "generation", "sum_gens_hours", "node_circles"),
    ("RE Penetration", "re_penetration", "ratio", "node_circles"),
    ("Nodal Prices (LMP)", "nodal_electricity_prices", "mean_hours", "node_circles"),
    ("Load Shedding", "loss_load", "sum_hours", "node_circles"),
    ("CO2 Emissions", "CO2_emissions", "sum_hours", "node_circles"),
    ("Curtailment", "curtailment", "sum_hours", "node_circles"),
    ("Gen. Investment", "gen_investment_power", "sum_gens", "node_circles"),
    ("Bat. Investment", "bat_investment_power", "sum_gens", "node_circles"),
]

_FLOW_VARIABLES = [
    ("Power Flow", "power_flow", "mean_hours", "flow_lines"),
    ("Fuel Transport Flow", "primary_energy", "fuel_transport", "fuel_flow_lines"),
]

_RISK_VARIABLES = [
    ("Risk Coefficient", "risk", "risk_coefficient", "risk_overlay"),
    ("Expected Annual Loss", "risk", "eal", "risk_overlay"),
    ("Composite Risk", "risk", "composite_risk", "risk_overlay"),
]

_RENEWABLE_KEYWORDS = {"solar", "wind", "biomass", "hydro", "hydroelectric", "otec", "renewable"}

# Keep backward compat
_RESULTS_VARIABLES = _NODE_VARIABLES + _FLOW_VARIABLES + _RISK_VARIABLES

# Map variable display name → set of HDF5 capability tags required.
# A variable whose requirements are not all met by the active HDF5 is
# hidden from the map's combo. Variables not listed are always shown
# (they depend on the operational scenario data which is implicit
# whenever the user even sees the panel) — except the Risk overlays
# which always depend on the workbench-injected risk data, not on the
# HDF5; they stay always-visible.
_VARIABLE_REQUIREMENTS: dict[str, set[str]] = {
    "Gen. Investment":     {"investment"},
    "Bat. Investment":     {"investment"},
    "Power Flow":          {"power_flow", "multi_node"},
    "Fuel Transport Flow": {"primary_energy"},
}


def _flat_datasets(grp) -> dict:
    """Collect all datasets from an HDF5 group, flattening nested groups.

    Generator/battery names may contain ``/`` which HDF5 interprets as a
    path separator, creating nested groups.  This helper recurses and
    returns ``{flat_name: dataset}`` pairs.

    Accepts both real ``h5py.Group`` / ``h5py.Dataset`` objects and the
    Phase-2 slicing proxies (``_SlicedGroupView`` / ``_SlicedDatasetView``)
    — the ``_GROUP_T`` / ``_DATASET_T`` type tuples bundle them so the
    isinstance checks below classify either layout correctly.
    """
    out: dict = {}

    def _walk(g, prefix=""):
        for k in g:
            item = g[k]
            full = f"{prefix} - {k}" if prefix else k
            if isinstance(item, _DATASET_T):
                out[full] = item
            elif isinstance(item, _GROUP_T):
                _walk(item, full)

    _walk(grp)
    return out


def _color_interp(
    val: float, min_val: float, max_val: float,
    color_min: str, color_max: str,
) -> str:
    """Interpolate between two hex colours."""
    if max_val <= min_val:
        t = 0.5
    else:
        t = max(0.0, min(1.0, (val - min_val) / (max_val - min_val)))
    r1, g1, b1 = int(color_min[1:3], 16), int(color_min[3:5], 16), int(color_min[5:7], 16)
    r2, g2, b2 = int(color_max[1:3], 16), int(color_max[3:5], 16), int(color_max[5:7], 16)
    r = int(r1 + t * (r2 - r1))
    g = int(g1 + t * (g2 - g1))
    b = int(b1 + t * (b2 - b1))
    return f"#{r:02x}{g:02x}{b:02x}"


# ── Screenshot helpers ───────────────────────────────────────────

import math


def _nice_ticks(lo: float, hi: float, target_count: int = 5) -> list[float]:
    """Return human-friendly tick positions between *lo* and *hi*."""
    span = hi - lo
    if span <= 0:
        return [lo]
    raw_step = span / max(target_count, 1)
    mag = 10 ** math.floor(math.log10(raw_step))
    residual = raw_step / mag
    if residual <= 1.5:
        nice = 1
    elif residual <= 3.5:
        nice = 2
    elif residual <= 7.5:
        nice = 5
    else:
        nice = 10
    step = nice * mag
    first = math.ceil(lo / step) * step
    ticks: list[float] = []
    v = first
    while v <= hi + step * 1e-9:
        ticks.append(round(v, 10))
        v += step
    return ticks


def _format_coord(val: float, *, is_lat: bool) -> str:
    """Format a decimal-degree value as DMS, e.g. ``21°53'24" N``."""
    if is_lat:
        suffix = "N" if val >= 0 else "S"
    else:
        suffix = "E" if val >= 0 else "W"
    v = abs(val)
    deg = int(v)
    m = (v - deg) * 60
    minutes = int(m)
    sec = int((m - minutes) * 60)
    return f"{deg}\u00b0{minutes:02d}'{sec:02d}\"{suffix}"


def _draw_north_arrow(painter, cx: float, cy: float, size: float = 20):
    """Draw a simple north-arrow compass at (*cx*, *cy*)."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QBrush, QColor, QFont, QPen, QPolygonF

    # Arrow body
    tip = QPointF(cx, cy - size)
    left = QPointF(cx - size * 0.35, cy + size * 0.4)
    right = QPointF(cx + size * 0.35, cy + size * 0.4)
    mid = QPointF(cx, cy + size * 0.1)

    # Dark half (right)
    painter.setPen(QPen(QColor(0, 0, 0), 0.8))
    painter.setBrush(QBrush(QColor(40, 40, 40)))
    painter.drawPolygon(QPolygonF([tip, mid, right]))

    # Light half (left)
    painter.setBrush(QBrush(QColor(255, 255, 255)))
    painter.drawPolygon(QPolygonF([tip, left, mid]))

    # "N" label
    font = QFont("Arial", max(8, int(size * 0.55)), QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(QPen(QColor(0, 0, 0)))
    from PySide6.QtGui import QFontMetrics
    fm = QFontMetrics(font)
    tw = fm.horizontalAdvance("N")
    painter.drawText(QPointF(cx - tw / 2, cy - size - 3), "N")


# ── Floating overlay ─────────────────────────────────────────────


class ResultsPanel(QWidget):
    """Compact floating overlay for selecting and rendering map results.

    Designed to float on top of the map widget.  Selecting a variable
    automatically renders it — no Apply / Clear buttons needed.
    """

    _OVERLAY_STYLE = """
        ResultsPanel {
            background: rgba(255, 255, 255, 0.92);
            border: 1px solid #bbb;
            border-radius: 8px;
        }
        QLabel { font-size: 11px; }
        QComboBox { font-size: 11px; min-width: 110px; }
        QPushButton { font-size: 11px; }
    """

    def __init__(self, map_widget: MapWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self._map_widget = map_widget
        self._output_dir: Optional[Path] = None
        self._h5_files: dict[str, Path] = {}
        # HDF5 base prefix per system: e.g. "systems/Cuba" or "" for legacy
        self._base_prefix: dict[str, str] = {}
        self._mga_h5_path: Optional[Path] = None
        self._node_coords: list[tuple[float, float]] = []
        self._gui_node_coords: list[tuple[float, float]] = []
        self._active = False
        self._suppress_render = False  # guard for cascading combo updates
        self._risk_data: list[dict] = []  # from risk workbench
        # Multi-layer render state. When several node variables are
        # shown at once their markers are nudged off the exact node
        # coordinate by ``_coord_offset`` (set per-variable) so they
        # don't overlap; ``_accumulate`` tells the per-layer renderers
        # NOT to clear the map (the batch clears once up front).
        self._coord_offset: tuple[float, float] = (0.0, 0.0)
        self._accumulate = False
        # Tracks whether a risk overlay is currently drawn, so we only
        # clear the risk layer on a risk→no-risk transition.
        self._risk_shown = False

        # Debounce timer for auto-render
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(200)
        self._render_timer.timeout.connect(self._do_render)

        self._build_ui()
        self.setStyleSheet(self._OVERLAY_STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hide()

    def register_result_variable(
        self,
        display_name: str,
        hdf5_key: str,
        aggregation: str,
        viz_type: str,
    ) -> None:
        """Register a new result variable from a plugin."""
        _RESULTS_VARIABLES.append((display_name, hdf5_key, aggregation, viz_type))
        if hasattr(self, "_var_combo"):
            self._var_combo.add_checkable_item(display_name, data=viz_type)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # Title row
        title_row = QHBoxLayout()
        title_row.setSpacing(4)
        self._title_label = QLabel(tr("results_panel.title"))
        self._title_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        title_row.addWidget(self._title_label)
        title_row.addStretch()

        self._screenshot_btn = QPushButton(tr("results_panel.screenshot_btn"))
        self._screenshot_btn.setFixedHeight(24)
        self._screenshot_btn.clicked.connect(self._on_screenshot)
        title_row.addWidget(self._screenshot_btn)
        layout.addLayout(title_row)

        # Row 1: System + Year
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        self._system_label = QLabel(tr("results_panel.system"))
        row1.addWidget(self._system_label)
        self._system_combo = QComboBox()
        self._system_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._system_combo.currentTextChanged.connect(self._on_system_changed)
        row1.addWidget(self._system_combo, stretch=1)

        self._year_label = QLabel(tr("results_panel.year"))
        row1.addWidget(self._year_label)
        self._year_combo = QComboBox()
        self._year_combo.setMinimumWidth(70)
        self._year_combo.currentTextChanged.connect(self._on_year_changed)
        row1.addWidget(self._year_combo)
        layout.addLayout(row1)

        # Row 2: Alt (hidden by default)
        self._alt_row = QHBoxLayout()
        self._alt_row.setSpacing(4)
        self._alt_label = QLabel(tr("results_panel.alternative"))
        self._alt_row.addWidget(self._alt_label)
        self._alt_combo = QComboBox()
        self._alt_combo.currentTextChanged.connect(self._on_alt_changed)
        self._alt_row.addWidget(self._alt_combo, stretch=1)
        self._alt_widget = QWidget()
        self._alt_widget.setLayout(self._alt_row)
        self._alt_widget.hide()
        layout.addWidget(self._alt_widget)

        # Row 3: Variable
        row3 = QHBoxLayout()
        row3.setSpacing(4)
        self._var_label = QLabel(tr("results_panel.variable"))
        row3.addWidget(self._var_label)
        self._var_combo = _CheckableComboBox()
        self._var_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        # ``_visible_var_indices`` maps the row position inside the
        # combo to the index in ``_RESULTS_VARIABLES``. The combo only
        # shows variables whose HDF5 dependencies are satisfied, but
        # the rest of the panel still keys off the canonical index in
        # the global table — this mapping bridges the two.
        self._visible_var_indices: list[int] = list(range(len(_RESULTS_VARIABLES)))
        self._populate_var_combo()
        # Flow layers (lines) are mutually exclusive — only one set of
        # arrows makes sense at once. Node layers (circles/pies) and
        # risk overlays may coexist (offset de-overlap handles nodes).
        self._var_combo.on_item_checked = self._enforce_layer_rules
        self._var_combo.selectionChanged.connect(self._schedule_render)
        row3.addWidget(self._var_combo, stretch=1)
        layout.addLayout(row3)

    def _populate_var_combo(self, capabilities: set[str] | None = None):
        """Refresh the variable combo against the active HDF5's
        capability set. Variables whose dependencies are not met are
        omitted from the menu so the user never picks something that
        would silently render nothing.

        Called once at construction (with no HDF5 known yet → every
        variable visible) and again whenever the active system / file
        changes."""
        # Snapshot the previously-checked display names so we can
        # restore them where they still apply.
        prev_checked = set()
        if hasattr(self, "_var_combo") and self._var_combo.model().rowCount() > 0:
            m = self._var_combo.model()
            for r in range(m.rowCount()):
                it = m.item(r)
                if it.checkState() == Qt.CheckState.Checked:
                    prev_checked.add(it.text())

        # Rebuild the menu from scratch.
        self._var_combo.clear()
        self._visible_var_indices = []
        for i, (display_name, _key, _agg, viz_type) in enumerate(_RESULTS_VARIABLES):
            req = _VARIABLE_REQUIREMENTS.get(display_name, set())
            if capabilities is not None and not req.issubset(capabilities):
                continue
            self._var_combo.add_checkable_item(display_name, data=viz_type)
            self._visible_var_indices.append(i)

        # Restore prior selection where the variable is still visible.
        m = self._var_combo.model()
        restored: list[int] = []
        for r in range(m.rowCount()):
            if m.item(r).text() in prev_checked:
                restored.append(r)
        if restored:
            self._var_combo.set_checked_rows(restored)
        elif m.rowCount() > 0:
            # Pre-check the first visible variable so the map shows
            # something on open (mirrors the old single-select default).
            self._var_combo.set_checked_rows([0])

    _FLOW_VIZ = {"flow_lines", "fuel_flow_lines"}

    def _enforce_layer_rules(self, model, checked_item):
        """When a flow-line variable is checked, uncheck any other
        flow-line variable (mutually exclusive). Node / risk layers are
        left untouched so they can stack."""
        viz = checked_item.data(Qt.ItemDataRole.UserRole)
        if viz not in self._FLOW_VIZ:
            return
        for r in range(model.rowCount()):
            it = model.item(r)
            if it is checked_item:
                continue
            if it.data(Qt.ItemDataRole.UserRole) in self._FLOW_VIZ:
                it.setCheckState(Qt.CheckState.Unchecked)

    # ------------------------------------------------------------------
    # Retranslation
    # ------------------------------------------------------------------

    def retranslateUi(self):
        """Update translatable strings."""
        self._title_label.setText(tr("results_panel.title"))
        self._screenshot_btn.setText(tr("results_panel.screenshot_btn"))
        self._system_label.setText(tr("results_panel.system"))
        self._year_label.setText(tr("results_panel.year"))
        self._alt_label.setText(tr("results_panel.alternative"))
        self._var_label.setText(tr("results_panel.variable"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_output_dir(self, path: str):
        """Point the panel at a results directory and scan for HDF5 files."""
        if not path:
            return
        self._output_dir = Path(path)
        self._scan_results()

    def set_gui_node_coords(self, coords: list[tuple[float, float]]):
        """Provide fallback node coordinates from the GUI model."""
        self._gui_node_coords = list(coords)

    def set_risk_data(self, risk_data: list[dict]):
        """Store risk assessment results for map rendering.

        risk_data: [{lat, lng, risk_coefficient, eal, composite_risk,
                     dominant_hazard, label, elements}, ...]
        """
        self._risk_data = risk_data
        # Re-render so a risk variable selected *before* the workbench
        # produced data picks it up immediately (the workbench calls
        # this after the user has already opened the results panel).
        if self._active:
            self._schedule_render()

    def _render_risk_overlay(self, metric: str):
        """Render risk assessment results on the map.

        metric: 'risk_coefficient', 'eal', or 'composite_risk'
        """
        # Clear only the risk layer — NOT the whole results layer —
        # so risk circles can coexist with node/flow results when both
        # are selected. The batch in _do_render clears everything once
        # up front.
        self._map_widget.clear_risk_layer()
        if not self._risk_data:
            return

        import math

        circles = []
        for d in self._risk_data:
            lat, lng = d.get("lat", 0), d.get("lng", 0)
            if lat == 0 and lng == 0:
                continue

            if metric == "risk_coefficient":
                value = d.get("risk_coefficient", 1.0)
                risk_idx = 1.0 - value
                label_val = f"\u03c1 = {value:.3f}"
            elif metric == "eal":
                value = d.get("eal", 0)
                risk_idx = min(1.0, math.log10(max(value, 1)) / 6)  # 0-1M range
                label_val = f"EAL = ${value:,.0f}/yr"
            else:  # composite_risk
                value = d.get("composite_risk", 0)
                risk_idx = min(1.0, value * 3)
                label_val = f"P = {value:.4f}"

            radius = max(6, min(25, 6 + 4 * math.log10(max(d.get("eal", 1), 1))))

            tooltip = (
                f"<b>{label_val}</b><br/>"
                f"<b>Dominant:</b> {d.get('dominant_hazard', '').replace('_', ' ').title()}<br/>"
                f"<b>Elements:</b> {d.get('elements', '')}"
            )
            circles.append({
                "lat": lat, "lng": lng,
                "radius": radius,
                "risk_index": risk_idx,
                "label": d.get("label", ""),
                "tooltip": tooltip,
            })

        if circles:
            self._map_widget.add_risk_circles(circles)
            self._map_widget.add_risk_legend(
                metric.replace("_", " ").title(),
                [
                    {"label": "Low", "color": "#27ae60"},
                    {"label": "Medium", "color": "#f1c40f"},
                    {"label": "High", "color": "#e67e22"},
                    {"label": "Very High", "color": "#e74c3c"},
                ],
            )

    def activate(self):
        """Called when the Results layer becomes visible."""
        self._active = True
        if self._output_dir:
            self._scan_results()
        self.show()
        self.raise_()

    def deactivate(self):
        """Called when leaving the Results layer."""
        self._active = False
        self._map_widget.clear_results()
        self.hide()

    def reposition(self, parent_width: int, _parent_height: int):
        """Reposition the overlay in the top-right of the parent area."""
        margin = 10
        self.adjustSize()
        x = parent_width - self.width() - margin
        y = margin
        self.move(max(margin, x), y)

    # ------------------------------------------------------------------
    # HDF5 scanning
    # ------------------------------------------------------------------

    def _scan_results(self):
        """Scan output directory for HDF5 result files.

        Files with a ``systems/`` group are split into per-system entries
        plus a "Global" entry.  Legacy files (no ``systems/``) are registered
        as a single entry.
        """
        self._suppress_render = True
        self._h5_files.clear()
        self._base_prefix.clear()
        self._system_combo.clear()

        if not self._output_dir or not self._output_dir.is_dir():
            self._suppress_render = False
            return

        # Collect all non-MGA HDF5 files, prefer most recent
        all_h5: list[Path] = []
        for p in self._output_dir.glob("*.h5"):
            if p.stem.startswith("mga_"):
                continue
            all_h5.append(p)
        if not all_h5:
            for p in self._output_dir.rglob("*.h5"):
                if p.stem.startswith("mga_"):
                    continue
                all_h5.append(p)

        # Sort: completed runs first (export_complete attr), then by
        # modification time (newest first). An interrupted run leaves a
        # skeleton file with empty detailed_results / summary_results
        # behind; picking it up by mtime alone was making the panel
        # appear empty (no year selector, no charts) without any
        # visible error. Skipping incomplete files unless none of the
        # candidates is complete keeps the GUI usable while still
        # surfacing partial results when that's all the user has.
        import h5py

        def _is_complete(p: Path) -> bool:
            try:
                with h5py.File(p, "r") as fh:
                    if not bool(fh.attrs.get("export_complete", False)):
                        return False
                    # Even if the flag is set, sanity-check that the
                    # year axis was populated before declaring the file
                    # usable — a partial export can flip the flag late.
                    sr = fh.get("summary_results")
                    return sr is not None and "year" in sr and sr["year"].shape[0] > 0
            except Exception:
                return False

        all_h5.sort(key=lambda p: (
            not _is_complete(p),       # complete files first (False < True)
            -p.stat().st_mtime,        # then newest first
        ))

        # Detect per-system structure
        for p in all_h5:
            try:
                with h5py.File(p, "r") as f:
                    # Multi-system runs publish their member names as a
                    # root attribute; we no longer require the (legacy)
                    # ``/systems/`` group to exist.
                    sub_names = f.attrs.get("subsystem_names")
                    if sub_names is None and "systems" in f:
                        sub_names = list(f["systems"].keys())
                    if sub_names is not None and len(sub_names) > 0:
                        names_list = [
                            n.decode() if isinstance(n, bytes) else str(n)
                            for n in sub_names
                        ]
                        for sname in names_list:
                            if sname not in self._h5_files:
                                self._h5_files[sname] = p
                                self._base_prefix[sname] = f"systems/{sname}"
                        if "Global" not in self._h5_files:
                            self._h5_files["Global"] = p
                            # Global uses root-level detailed_results (merged data)
                            self._base_prefix["Global"] = ""
                    else:
                        name = p.stem.replace("results_", "").replace("_results", "")
                        if name not in self._h5_files:
                            self._h5_files[name] = p
                            self._base_prefix[name] = ""
            except Exception:
                pass

        if self._h5_files:
            self._system_combo.addItems(list(self._h5_files.keys()))
            # Default to "Global" when present: it's the unified
            # geographic view (all systems' nodes on one map, with the
            # node indexing already aligned to the 10-node global
            # space). Per-system entries only show that system's nodes
            # — so a multi-system run would otherwise hide every
            # system but the first.
            if "Global" in self._h5_files:
                self._system_combo.setCurrentText("Global")
        else:
            self._system_combo.addItem(tr("results_panel.no_results"))
        self._suppress_render = False

    def _on_system_changed(self, system_name: str):
        self._suppress_render = True
        self._year_combo.clear()
        path = self._h5_files.get(system_name)
        if not path or not path.exists():
            self._suppress_render = False
            return

        bp = self._base_prefix.get(system_name, "")

        try:
            import h5py
            from esfex.visualization.panels.results_charts import _detect_capabilities
            with h5py.File(path, "r") as f:
                # Per-system mirrors were removed by the Phase-2 refactor;
                # the slicing helpers (_open_summary_results /
                # _open_system_config) transparently derive a per-system
                # view from the root block when the legacy mirror is
                # absent, so the lookup works against both old and new
                # result files.
                sg = _open_summary_results(f, bp)
                if sg is not None and "year" in sg:
                    years = sorted(set(int(y) for y in sg["year"][:]))
                    for y in years:
                        self._year_combo.addItem(str(y))

                # Refresh the variable combo against the active HDF5's
                # capability set so variables whose data is missing
                # (no MGA, no primary energy, single-node, …) drop out
                # of the menu instead of silently rendering nothing.
                self._populate_var_combo(_detect_capabilities(f))

                self._node_coords = []
                cfg = _open_system_config(f, bp)
                if cfg is not None and "nodes" in cfg:
                    nodes_grp = cfg["nodes"]
                    if "latitude" in nodes_grp and "longitude" in nodes_grp:
                        lats = nodes_grp["latitude"][:]
                        lngs = nodes_grp["longitude"][:]
                        self._node_coords = [
                            (float(lats[i]), float(lngs[i]))
                            for i in range(len(lats))
                        ]
        except Exception as e:
            QMessageBox.warning(self, tr("common.error"), tr("messages.hdf5_error_msg", e=e))

        # Detect MGA data inside the main HDF5 (/mga/ group)
        try:
            import h5py as _h5
            with _h5.File(path, "r") as _f:
                has_mga = "mga" in _f
        except Exception:
            has_mga = False
        if has_mga:
            self._mga_h5_path = path
            self._load_mga_alternatives()
            self._alt_widget.show()
        else:
            self._mga_h5_path = None
            self._alt_widget.hide()

        self._suppress_render = False
        # Trigger render now that combos are populated
        self._schedule_render()

    def _load_mga_alternatives(self):
        """Populate alternative combo from /mga/ group in main HDF5."""
        self._alt_combo.clear()
        self._alt_combo.addItem("-- None --")
        if not self._mga_h5_path or not self._mga_h5_path.exists():
            return
        try:
            import h5py
            with h5py.File(self._mga_h5_path, "r") as f:
                if "mga" not in f:
                    return
                mga = f["mga"]
                num_alts = int(mga.attrs.get("num_alternatives", 0))
                for k in range(num_alts):
                    grp_key = f"alternative_{k}"
                    if grp_key in mga:
                        if k == 0:
                            self._alt_combo.addItem("Cost-Optimal (0)")
                        else:
                            self._alt_combo.addItem(f"Alternative {k}")
        except Exception:
            pass

    def _on_alt_changed(self, text: str):
        self._schedule_render()

    def _on_year_changed(self, year_str: str):
        self._schedule_render()

    # ------------------------------------------------------------------
    # Auto-render (debounced)
    # ------------------------------------------------------------------

    def _schedule_render(self):
        """Schedule an auto-render after a short debounce period."""
        if self._suppress_render or not self._active:
            return
        self._render_timer.start()

    def _do_render(self):
        """Render every checked variable at once (called by the debounce
        timer).

        Layers stack: node variables (circles / pies) are drawn with a
        per-variable de-overlap offset; at most one flow variable (the
        UI keeps those mutually exclusive); risk overlays on top. The
        whole results layer is cleared once up front, then each layer
        is appended (``_accumulate`` keeps the per-layer renderers from
        clearing each other).
        """
        if not self._active:
            return
        system_name = self._system_combo.currentText()
        year_str = self._year_combo.currentText()
        rows = self._var_combo.checked_rows()

        # Nothing selected → clear and stop.
        if not year_str or not rows:
            self._map_widget.clear_results()
            return

        path = self._h5_files.get(system_name)
        if not path:
            return
        if not Path(path).exists():
            # Stale registry entry (file moved/deleted, or output dir
            # changed). Bail quietly instead of letting h5py raise a
            # noisy FileNotFoundError traceback every render tick.
            import sys
            print(f"[Results] results file not found: {path}", file=sys.stderr)
            return

        year = int(year_str)
        # ``rows`` indexes the visible combo entries — translate via
        # ``_visible_var_indices`` to indices in ``_RESULTS_VARIABLES``
        # (the two differ whenever a capability filter hid an entry).
        selected = [
            _RESULTS_VARIABLES[self._visible_var_indices[r]]
            for r in rows
            if r < len(self._visible_var_indices)
        ]
        node_vars = [v for v in selected if v[3] in ("pie_chart", "node_circles")]
        flow_vars = [v for v in selected if v[3] in self._FLOW_VIZ]
        risk_vars = [v for v in selected if v[3] == "risk_overlay"]

        bp = self._base_prefix.get(system_name, "")

        # Clear the node/flow results layer once; everything below
        # appends. The risk layer is touched ONLY on a risk→no-risk
        # transition (so a deselected risk overlay doesn't linger),
        # never on a normal node/flow render — calling clear_risk_layer
        # unconditionally was blanking the node/flow layers.
        self._map_widget.clear_results()
        if not risk_vars and self._risk_shown:
            try:
                self._map_widget.clear_risk_layer()
            except Exception:
                pass
            self._risk_shown = False
        mga_alt = self._get_selected_mga_alt()

        try:
            import h5py
            with h5py.File(path, "r") as f:
                scenario_key = f"year_{year}_threshold_0"
                # _open_scenario returns the legacy per-system group
                # when present, or a sliced proxy onto the root scenario
                # otherwise — see the Phase-2 refactor in
                # results_charts. Either way the downstream renderers
                # access it through the same h5py-Group-like API.
                try:
                    scenario = _open_scenario(f, bp, scenario_key)
                except KeyError:
                    scenario = None

                self._accumulate = True
                try:
                    # ── Node layers (circles / pies) with de-overlap ──
                    k = len(node_vars)
                    for i, (display, key, agg, viz) in enumerate(node_vars):
                        self._coord_offset = self._compute_offset(i, k)
                        is_invest = key in ("gen_investment_power",
                                             "bat_investment_power")
                        if mga_alt >= 0 and self._mga_h5_path and is_invest:
                            self._apply_mga_investment(mga_alt, year, key, display)
                            continue
                        if scenario is None:
                            continue
                        if viz == "pie_chart":
                            self._render_pie_charts(
                                scenario, f, display, year, agg, base_prefix=bp,
                            )
                        elif viz == "node_circles":
                            vals = self._extract_node_values(
                                scenario, key, agg, f, year, base_prefix=bp,
                            )
                            if vals is not None:
                                self._render_node_circles(vals, display)
                    self._coord_offset = (0.0, 0.0)

                    # ── Flow layer (UI guarantees at most one) ──
                    if scenario is not None:
                        for (display, key, agg, viz) in flow_vars:
                            if viz == "flow_lines":
                                self._render_flow_lines(scenario, key, display,
                                                        f, base_prefix=bp)
                            elif viz == "fuel_flow_lines":
                                self._render_fuel_flow_lines(scenario, f, display)
                finally:
                    self._accumulate = False
                    self._coord_offset = (0.0, 0.0)
        except Exception:
            import traceback
            traceback.print_exc()

        # ── Risk overlays ── rendered independently of the HDF5 block
        # above (they read pre-stored risk data, not the scenario) so a
        # failure drawing node/flow layers never suppresses them.
        for (display, key, agg, viz) in risk_vars:
            try:
                self._render_risk_overlay(agg)
                self._risk_shown = True
            except Exception:
                import traceback
                traceback.print_exc()

    # ------------------------------------------------------------------
    # Screenshot
    # ------------------------------------------------------------------

    def _on_screenshot(self):
        """Capture a framed screenshot of the map with coordinate axes."""
        save_path, _ = QFileDialog.getSaveFileName(
            self, tr("results_panel.screenshot_btn"),
            "map_results.png",
            "PNG (*.png);;JPEG (*.jpg);;All Files (*)",
        )
        if not save_path:
            return
        # Get bounds FIRST (before any QEventLoop nesting)
        bounds = self._map_widget.get_visible_bounds()

        # Hide overlay and ALL map controls for a clean capture
        self.hide()
        from PySide6.QtCore import QEventLoop, QTimer as _QTimer
        from PySide6.QtWidgets import QApplication

        # Inject a CSS rule that hides ALL Leaflet controls; wait for
        # the JS callback so we know the DOM change has been applied.
        hide_js = (
            "var s=document.createElement('style');"
            "s.id='_esfex_screenshot_hide';"
            "s.textContent='.leaflet-control-container{display:none!important}';"
            "document.head.appendChild(s);"
            "true;"
        )
        done_loop = QEventLoop()
        self._map_widget.page().runJavaScript(hide_js, lambda _: done_loop.quit())
        _QTimer.singleShot(3000, done_loop.quit)
        done_loop.exec()

        # Give the web renderer a moment to repaint without controls
        wait_loop = QEventLoop()
        _QTimer.singleShot(250, wait_loop.quit)
        wait_loop.exec()
        QApplication.processEvents()

        map_pixmap = self._map_widget.grab()

        # Remove the injected style to restore controls
        restore_js = (
            "var s=document.getElementById('_esfex_screenshot_hide');"
            "if(s) s.remove();"
            "true;"
        )
        done_loop2 = QEventLoop()
        self._map_widget.page().runJavaScript(restore_js, lambda _: done_loop2.quit())
        _QTimer.singleShot(3000, done_loop2.quit)
        done_loop2.exec()

        self.show()
        self.raise_()

        framed = self._compose_framed_screenshot(map_pixmap, bounds)
        # Save at 300 DPI
        img = framed.toImage()
        dpm = int(300 / 0.0254)  # dots per metre for 300 DPI
        img.setDotsPerMeterX(dpm)
        img.setDotsPerMeterY(dpm)
        ok = img.save(save_path)
        if ok:
            QMessageBox.information(
                self, tr("results_panel.screenshot_btn"),
                tr("results_panel.screenshot_saved", path=save_path),
            )
        else:
            QMessageBox.warning(
                self, tr("common.error"),
                "Failed to save screenshot.",
            )

    @staticmethod
    def _compose_framed_screenshot(map_pixmap, bounds):
        """Add coordinate frame (lat/lon axes) and north arrow to a map grab.

        Parameters
        ----------
        map_pixmap : QPixmap
            Raw grab of the map widget.
        bounds : tuple | None
            (south, west, north, east) in decimal degrees, or *None*.

        Returns
        -------
        QPixmap
            Composed image with margins, tick labels, and compass.
        """
        from PySide6.QtCore import QPointF, QRectF, Qt
        from PySide6.QtGui import (
            QColor,
            QFont,
            QFontMetrics,
            QPainter,
            QPen,
            QPixmap,
            QPolygonF,
        )

        margin_left = 78
        margin_top = 56
        margin_right = 12
        margin_bottom = 12

        mw = map_pixmap.width()
        mh = map_pixmap.height()
        total_w = margin_left + mw + margin_right
        total_h = margin_top + mh + margin_bottom

        out = QPixmap(total_w, total_h)
        out.fill(QColor(255, 255, 255))

        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw the map
        p.drawPixmap(margin_left, margin_top, map_pixmap)

        # Frame around the map
        pen = QPen(QColor(0, 0, 0), 1.5)
        p.setPen(pen)
        p.drawRect(QRectF(
            margin_left - 0.5, margin_top - 0.5,
            mw + 1, mh + 1,
        ))

        # --- Coordinate ticks ---
        if bounds is not None:
            south, west, north, east = bounds
        else:
            south, west, north, east = -90.0, -180.0, 90.0, 180.0

        tick_font = QFont("Arial", 14)
        tick_font.setBold(True)
        p.setFont(tick_font)
        p.setPen(QPen(QColor(0, 0, 0)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        fm = QFontMetrics(tick_font)
        tick_len = 6

        # --- Latitude ticks (left axis, rotated -90°) ---
        lat_ticks = _nice_ticks(south, north, target_count=5)
        for lat in lat_ticks:
            frac = (north - lat) / (north - south) if north != south else 0.5
            y = float(margin_top + frac * mh)
            p.drawLine(QPointF(float(margin_left - tick_len), y),
                       QPointF(float(margin_left), y))
            label = _format_coord(lat, is_lat=True)
            tw = fm.horizontalAdvance(label)
            p.save()
            p.translate(float(margin_left - tick_len - 3), y)
            p.rotate(-90)
            p.drawText(QPointF(-tw / 2.0, 0.0), label)
            p.restore()

        # --- Longitude ticks (top axis) ---
        lon_ticks = _nice_ticks(west, east, target_count=5)
        for lon in lon_ticks:
            frac = (lon - west) / (east - west) if east != west else 0.5
            x = float(margin_left + frac * mw)
            p.drawLine(QPointF(x, float(margin_top - tick_len)),
                       QPointF(x, float(margin_top)))
            label = _format_coord(lon, is_lat=False)
            tw = fm.horizontalAdvance(label)
            p.drawText(QPointF(x - tw / 2.0,
                               float(margin_top - tick_len - 3)), label)

        # --- North arrow (top-left corner inside the map) ---
        arrow_size = 40
        # The "N" label extends ~size*0.55 pt above the tip, so we need
        # enough room: size (body) + font-height (~size*0.7) + small gap.
        arrow_pad = int(arrow_size * 1.85) + 15
        _draw_north_arrow(
            p,
            margin_left + arrow_pad,
            margin_top + arrow_pad,
            size=arrow_size,
        )

        p.end()
        return out

    # ------------------------------------------------------------------
    # MGA helpers
    # ------------------------------------------------------------------

    def _get_selected_mga_alt(self) -> int:
        """Return the selected MGA alternative index, or -1 if none."""
        if not self._alt_widget.isVisible():
            return -1
        text = self._alt_combo.currentText()
        if not text or text.startswith("--"):
            return -1
        if "0" in text and "Optimal" in text:
            return 0
        try:
            return int(text.split()[-1])
        except (ValueError, IndexError):
            return -1

    def _apply_mga_investment(self, alt_idx: int, year: int,
                              hdf5_key: str, display_name: str):
        """Render MGA alternative investment data on the map."""
        try:
            import h5py
            with h5py.File(self._mga_h5_path, "r") as f:
                if "mga" not in f:
                    return
                mga = f["mga"]
                mga_years = mga.attrs.get("years", [])
                if hasattr(mga_years, 'tolist'):
                    mga_years = mga_years.tolist()

                grp_key = f"alternative_{alt_idx}"
                if grp_key not in mga:
                    return

                grp = mga[grp_key]

                if hdf5_key == "gen_investment_power":
                    ds_key = "gen_investment"
                else:
                    ds_key = "bat_power_investment"

                if ds_key not in grp:
                    return

                data = grp[ds_key][:]  # (years, techs, nodes)

                if year in mga_years:
                    y_idx = mga_years.index(year)
                    node_values = data[y_idx].sum(axis=0)
                else:
                    node_values = data.sum(axis=0).sum(axis=0)

                alt_label = "Optimal" if alt_idx == 0 else f"Alt {alt_idx}"
                title = f"{display_name} — {alt_label} ({year})"
                self._render_node_circles(node_values, title)

        except Exception:
            import traceback
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Data extraction
    # ------------------------------------------------------------------

    def _get_coords(self) -> list[tuple[float, float]]:
        """Return node coordinates, falling back to GUI model.

        When ``_coord_offset`` is set (multi-node-layer render), every
        coordinate is nudged by it so overlapping node markers fan out
        around their node instead of stacking on the exact same point.
        """
        base = self._node_coords if self._node_coords else self._gui_node_coords
        dlat, dlng = self._coord_offset
        if dlat or dlng:
            return [(lat + dlat, lng + dlng) for lat, lng in base]
        return base

    def _coord_span(self) -> float:
        """Characteristic geographic size of the node set (max of the
        lat/lng extents). Used to scale the de-overlap offset so it's
        proportional to the region, never hard-coded degrees."""
        base = self._node_coords if self._node_coords else self._gui_node_coords
        if len(base) < 2:
            return 0.5  # lone node — a small default in degrees
        lats = [c[0] for c in base]
        lngs = [c[1] for c in base]
        return max(max(lats) - min(lats), max(lngs) - min(lngs), 0.1)

    def _compute_offset(self, index: int, total: int) -> tuple[float, float]:
        """Radial de-overlap offset for node-layer *index* of *total*.

        A single layer stays centred on the node (no offset). With ≥2
        layers each is placed on a small ring around the node — radius
        ~4% of the region span — keeping every marker visually attached
        to its node while never overlapping the others.
        """
        if total <= 1:
            return (0.0, 0.0)
        import math
        radius = self._coord_span() * 0.04
        angle = 2.0 * math.pi * index / total
        return (radius * math.sin(angle), radius * math.cos(angle))

    def _extract_node_values(
        self, scenario: Any, hdf5_key: str,
        aggregation: str, root: Any,
        year: int = 0, base_prefix: str = "",
    ) -> Optional[np.ndarray]:
        """Extract per-node scalar values from HDF5 scenario."""

        if aggregation == "sum_gens_hours":
            if hdf5_key not in scenario:
                return None
            datasets = _flat_datasets(scenario[hdf5_key])
            total = None
            for gen_name, ds in datasets.items():
                data = ds[:]
                gen_sum = np.atleast_1d(data.sum(axis=-1))
                if total is None:
                    total = gen_sum.copy()
                else:
                    n = min(len(total), len(gen_sum))
                    total[:n] += gen_sum[:n]
            return total

        elif aggregation == "ratio":
            if "generation" not in scenario:
                return None
            datasets = _flat_datasets(scenario["generation"])
            resolve, _ = self._tech_resolver(root, base_prefix)
            total_gen = None
            re_gen = None
            for gen_name, ds in datasets.items():
                data = ds[:]
                gen_sum = np.atleast_1d(data.sum(axis=-1))
                total_gen = gen_sum.copy() if total_gen is None else total_gen + gen_sum
                info = resolve(gen_name)
                if info is not None:
                    is_re = info.get("category") in ("renewable", "rooftop")
                else:
                    is_re = self._is_type_renewable(self._extract_fuel_from_name(gen_name))
                if is_re:
                    re_gen = gen_sum.copy() if re_gen is None else re_gen + gen_sum
            if total_gen is None or re_gen is None:
                return None
            with np.errstate(divide="ignore", invalid="ignore"):
                return np.where(total_gen > 0, re_gen / total_gen, 0.0)

        elif aggregation == "mean_hours":
            if hdf5_key not in scenario:
                return None
            return np.atleast_1d(scenario[hdf5_key][:].mean(axis=-1))

        elif aggregation == "sum_hours":
            if hdf5_key not in scenario:
                return None
            return np.atleast_1d(scenario[hdf5_key][:].sum(axis=-1))

        elif aggregation == "sum_gens":
            # Try reading from dataset first
            if hdf5_key in scenario:
                datasets = _flat_datasets(scenario[hdf5_key])
                total = None
                for name, ds in datasets.items():
                    data = np.atleast_1d(ds[:].astype(float))
                    if total is None:
                        total = data.copy()
                    else:
                        n = min(len(total), len(data))
                        total[:n] += data[:n]
                return total

            # Fallback: compute cumulative from investment attrs.
            # The new {t_idx: {n_idx: mw}} layout lets us drop each
            # value on its real node instead of stacking everything
            # at node 0.
            if year > 0:
                cum = self._cumulative_tech_investments(root, year, base_prefix=base_prefix)
                rng = _system_node_range(root, base_prefix) if base_prefix else None
                n_offset = rng[0] if rng is not None else 0
                n_nodes = max(1, len(self._get_coords()))

                def _by_node(d: dict[int, dict[int, float]]) -> np.ndarray:
                    arr = np.zeros(n_nodes)
                    for per_node in d.values():
                        for n_idx, mw in per_node.items():
                            local_idx = n_idx - n_offset
                            if 0 <= local_idx < n_nodes:
                                arr[local_idx] += mw
                    return arr

                if hdf5_key == "gen_investment_power" and cum["gen"]:
                    arr = _by_node(cum["gen"])
                    return arr if arr.sum() > 0.01 else None
                elif hdf5_key == "bat_investment_power" and cum["bat_pow"]:
                    arr = _by_node(cum["bat_pow"])
                    return arr if arr.sum() > 0.01 else None
            return None

        return None

    def _get_renewable_flags(self, root: Any, base_prefix: str = "") -> list[bool]:
        """Get renewable flags from system_configuration."""
        sc = _open_system_config(root, base_prefix)
        flags: list[bool] = []
        if sc is None:
            return flags
        if "generators" not in sc:
            return flags
        gen_group = sc["generators"]
        # Iterate the group directly — the filter proxy preserves the
        # original (global) HDF5 keys for the subsystem's generators
        # and may skip indices, so ``range(num_generators)`` no longer
        # walks the right set.
        for gen_key in gen_group:
            is_re = gen_group[gen_key].attrs.get("is_renewable", False)
            if isinstance(is_re, (bytes, str)):
                is_re = str(is_re).lower() in ("true", "1", "yes")
            flags.append(bool(is_re))
        return flags

    def _get_gen_info(self, root: Any, base_prefix: str = "") -> list[dict]:
        """Get generator name + type info from system_configuration."""
        sc = _open_system_config(root, base_prefix)
        info: list[dict] = []
        if sc is None:
            return info
        if "generators" not in sc:
            return info
        gen_group = sc["generators"]
        for gen_key in gen_group:
            attrs = gen_group[gen_key].attrs
            name = attrs.get("name", gen_key)
            if isinstance(name, bytes):
                name = name.decode()
            gen_type = attrs.get("type", "")
            if isinstance(gen_type, bytes):
                gen_type = gen_type.decode()
            info.append({"name": name, "type": gen_type})
        return info


    @staticmethod
    def _extract_fuel_from_name(name: str) -> str:
        """Extract fuel/technology label from a dataset name.

        Handles patterns like:
          "Cuba - Agg. Fuel_oil (Artemisa)" → "Fuel Oil"
          "Investment Solar PV ~87MW" → "Solar"
          "IslaJuventud/Solar (La Fe)" → "Solar"
        """
        import re
        from esfex.visualization.theme import get_generation_colors
        color_keys = list(get_generation_colors().keys())

        # Common fuel names to detect
        _FUEL_LABELS = {
            "fuel_oil": "Fuel Oil", "fuel oil": "Fuel Oil",
            "solar": "Solar", "sun": "Solar",
            "wind": "Wind", "biomass": "Biomass",
            "diesel": "Diesel engines", "gas": "Gas turbines",
            "hydro": "Hydro", "nuclear": "Nuclear",
            "otec": "OTEC", "coal": "Coal",
            "hydrogen": "Hydrogen", "other": "Other",
        }

        nl = name.lower()

        # Pattern: "System - Agg. FUEL (Location)"
        m = re.match(r'.+[-–]\s*Agg\.\s*(\w+)', name)
        if m:
            fuel = m.group(1)
            label = _FUEL_LABELS.get(fuel.lower())
            if label:
                return label

        # Try matching against known fuel labels
        for fuel_key, label in _FUEL_LABELS.items():
            if fuel_key in nl:
                return label

        # Try matching against color keys
        for ck in color_keys:
            if ck.lower() in nl:
                return ck

        return name


    @staticmethod
    def _is_type_renewable(tech_type: str) -> bool:
        """Check if a technology type string represents a renewable source."""
        tl = tech_type.lower()
        return any(kw in tl for kw in _RENEWABLE_KEYWORDS)

    @staticmethod
    def _tech_index_to_label(root: Any, base_prefix: str = "") -> dict[int, str]:
        """Map technology index → technology name from HDF5 config."""
        sc = _open_system_config(root, base_prefix)
        def _d(v):
            return v.decode() if isinstance(v, bytes) else str(v)

        labels: dict[int, str] = {}
        if sc is not None and "technologies" in sc:
            tg = sc["technologies"]
            for tk in tg:
                # tk = "technology_0", "technology_1", …
                try:
                    idx = int(tk.split("_")[-1])
                except (ValueError, IndexError):
                    continue
                labels[idx] = _d(tg[tk].attrs.get("name", tk))
        return labels

    @staticmethod
    def _bat_tech_index_to_label(root: Any, base_prefix: str = "") -> dict[int, str]:
        """Map battery technology index → name from HDF5 config."""
        sc = _open_system_config(root, base_prefix)
        def _d(v):
            return v.decode() if isinstance(v, bytes) else str(v)

        labels: dict[int, str] = {}
        if sc is not None and "battery_technologies" in sc:
            btg = sc["battery_technologies"]
            for tk in btg:
                try:
                    idx = int(tk.split("_")[-1])
                except (ValueError, IndexError):
                    continue
                labels[idx] = _d(btg[tk].attrs.get("name", tk))
        return labels

    @staticmethod
    def _cumulative_tech_investments(
        root: Any, up_to_year: int, base_prefix: str = "",
    ) -> dict[str, dict[int, dict[int, float]]]:
        """Sum technology investment attrs from all years <= *up_to_year*,
        keyed by **technology index AND node index** so the renderer
        can place each MW value on the right node of the map.

        Returns ``{"gen": {t_idx: {n_idx: mw}}, "bat_pow": …, "bat_cap": …}``.

        Post-Phase-2 the per-system mirror under
        ``/systems/{name}/detailed_results/`` is gone — the
        ``investment_*_power_{t}_{n}`` attrs live only on the ROOT
        scenario, keyed by the *global* node index. When
        ``base_prefix`` selects a subsystem we therefore read root
        attrs and filter each one through the subsystem's node range
        (mirrors what ``dashboard_loader._investment_mw`` does)."""
        gen_inv: dict[int, dict[int, float]] = {}
        bat_pow: dict[int, dict[int, float]] = {}
        bat_cap: dict[int, dict[int, float]] = {}

        if "detailed_results" not in root:
            return {"gen": gen_inv, "bat_pow": bat_pow, "bat_cap": bat_cap}

        rng = _system_node_range(root, base_prefix) if base_prefix else None
        dr = root["detailed_results"]
        for sc_key in dr:
            sc = dr[sc_key]
            sc_year = int(sc.attrs.get("year", 0))
            if sc_year == 0 or sc_year > up_to_year:
                continue
            for attr_key in sc.attrs:
                if not attr_key.startswith("investment_"):
                    continue
                val = float(sc.attrs[attr_key])
                if val < 0.001:
                    continue
                rest = attr_key[len("investment_"):]  # e.g. "tech_investment_power_0_8"
                parts = rest.split("_")
                try:
                    # All variants encode the global node index in the
                    # last underscore-segment.
                    n_idx = int(parts[-1])
                    if rng is not None and not (rng[0] <= n_idx < rng[1]):
                        continue
                    if rest.startswith("tech_investment_power_") and len(parts) >= 5:
                        t_idx = int(parts[3])
                        d = gen_inv.setdefault(t_idx, {})
                        d[n_idx] = d.get(n_idx, 0.0) + val
                    elif rest.startswith("bat_tech_investment_power_") and len(parts) >= 6:
                        bt_idx = int(parts[4])
                        d = bat_pow.setdefault(bt_idx, {})
                        d[n_idx] = d.get(n_idx, 0.0) + val
                    elif rest.startswith("bat_tech_investment_capacity_") and len(parts) >= 6:
                        bt_idx = int(parts[4])
                        d = bat_cap.setdefault(bt_idx, {})
                        d[n_idx] = d.get(n_idx, 0.0) + val
                except (ValueError, IndexError):
                    continue

        return {"gen": gen_inv, "bat_pow": bat_pow, "bat_cap": bat_cap}

    # ------------------------------------------------------------------
    # Renderers
    # ------------------------------------------------------------------

    def _tech_resolver(self, root: Any, base_prefix: str = ""):
        """Return ``(resolve, label_color)`` for mapping HDF5 component
        names to technology labels + colours the SAME way the Results
        Viewer charts do — via each component's explicit ``technology``
        field matched to the technology table's ``key`` (no name/fuel
        heuristic). Keeps the map's pie slices / categories consistent
        with the dialog.

        ``resolve(name)`` returns the info dict ``{label,color,category}``
        or ``None`` (caller falls back). ``label_color`` is filled as a
        side effect of resolving, so callers can look up a slice colour
        by its label afterwards.
        """
        from esfex.visualization.panels.results_charts import (
            _build_gen_tech_map, _resolve_gen_tech,
            _load_gen_configs, _load_bat_configs,
            _load_tech_configs, _load_bat_tech_configs,
        )
        gen_tech_map = _build_gen_tech_map(
            _load_gen_configs(root, base_prefix),
            _load_tech_configs(root, base_prefix),
            _load_bat_configs(root, base_prefix),
            _load_bat_tech_configs(root, base_prefix),
        )
        label_color: dict[str, str] = {}

        def resolve(name: str):
            info = _resolve_gen_tech(name, gen_tech_map)
            if info and info.get("label") and info.get("color"):
                label_color.setdefault(info["label"], info["color"])
            return info

        return resolve, label_color

    def _render_pie_charts(self, scenario: Any, root: Any,
                           title: str, year: int,
                           aggregation: str = "mix_by_gen",
                           base_prefix: str = ""):
        if aggregation == "installed_capacity":
            self._render_installed_capacity_pies(scenario, root, title, year,
                                                  base_prefix=base_prefix)
            return
        if aggregation == "re_nonre":
            self._render_re_composition_pies(scenario, root, title, year,
                                              base_prefix=base_prefix)
            return

        if "generation" not in scenario:
            return

        coords = self._get_coords()
        if not coords:
            return

        from esfex.visualization.theme import (
            get_generation_colors,
            get_generation_default_color,
        )
        gen_colors = get_generation_colors()
        default_color = get_generation_default_color()

        gen_datasets = _flat_datasets(scenario["generation"])
        if not gen_datasets:
            return

        n_nodes = min(len(coords), max(
            ds[:].shape[0] for ds in gen_datasets.values()
        ))

        # Aggregate by technology via the explicit ``technology`` field
        # (same resolution as the Results Viewer charts).
        resolve, label_color = self._tech_resolver(root, base_prefix)
        tech_totals: dict[str, np.ndarray] = {}
        for gn, ds in gen_datasets.items():
            data = ds[:]
            gen_total = np.atleast_1d(data.sum(axis=-1))
            info = resolve(gn)
            tech_type = info["label"] if info and info.get("label") \
                else self._extract_fuel_from_name(gn)
            if tech_type in tech_totals:
                n = min(len(tech_totals[tech_type]), len(gen_total))
                tech_totals[tech_type][:n] += gen_total[:n]
            else:
                tech_totals[tech_type] = gen_total.copy()

        node_data: dict[int, list[dict]] = {}
        for tech_type, totals in tech_totals.items():
            # Priority: technology colour from the map > theme > default
            color = label_color.get(tech_type) or gen_colors.get(tech_type, default_color)
            for ni in range(min(n_nodes, len(totals))):
                val = float(totals[ni])
                if val > 0.01:
                    node_data.setdefault(ni, []).append({
                        "value": round(val, 2),
                        "color": color,
                        "label": tech_type,
                    })

        self._render_pie_data(node_data, n_nodes, coords, title, year)

    def _render_installed_capacity_pies(self, scenario: Any, root: Any,
                                        title: str, year: int,
                                        base_prefix: str = ""):
        coords = self._get_coords()
        if not coords:
            return

        sc = _open_system_config(root, base_prefix)
        if sc is None or "generators" not in sc:
            return

        from esfex.visualization.theme import (
            get_generation_colors,
            get_generation_default_color,
        )
        gen_colors = get_generation_colors()
        default_color = get_generation_default_color()

        # Resolve label + colour via the explicit ``technology`` field
        # (same as the Results Viewer); fall back to the legacy fuel
        # heuristic only for components with no resolvable technology.
        resolve, label_color = self._tech_resolver(root, base_prefix)

        def _label_and_color(name: str) -> str:
            info = resolve(name)
            if info and info.get("label"):
                return info["label"]
            return self._extract_fuel_from_name(name)

        n_nodes = len(coords)

        # Accumulate capacity by technology type
        tech_caps: dict[str, np.ndarray] = {}

        def _add(tech_type: str, cap_arr: np.ndarray):
            cap = np.atleast_1d(cap_arr)
            if tech_type in tech_caps:
                n = min(len(tech_caps[tech_type]), len(cap))
                tech_caps[tech_type][:n] += cap[:n]
            else:
                tech_caps[tech_type] = cap.copy()

        def _decode(val):
            return val.decode() if isinstance(val, bytes) else str(val)

        # 1. Original generators — rated_power from config
        gen_cfg = sc["generators"]
        # Iterate the (filtered) generator group directly — the proxy
        # preserves global HDF5 indices, so range(num_generators) walks
        # the wrong set under a subsystem prefix.
        for gen_key in gen_cfg:
            attrs = gen_cfg[gen_key].attrs
            name = _decode(attrs.get("name", gen_key))
            tech_type = _label_and_color(name)

            if "rated_power" in gen_cfg[gen_key]:
                rp = np.atleast_1d(gen_cfg[gen_key]["rated_power"][:])
            else:
                rp_val = attrs.get("rated_power", 0)
                if isinstance(rp_val, (bytes, str)):
                    rp_val = str(rp_val).strip("[] ")
                    rp = np.array([float(x) for x in rp_val.split(",") if x.strip()])
                else:
                    rp = np.atleast_1d(np.asarray(rp_val, dtype=float))
            _add(tech_type, rp)

        # 2. Investment capacities — cumulative from attrs across years.
        # Each {t_idx: {n_idx: mw}} entry tells us which node received
        # the investment, so we drop it on the matching node instead of
        # piling every MW onto node 0.
        cum_inv = self._cumulative_tech_investments(root, year, base_prefix=base_prefix)
        tech_cfg = self._tech_index_to_label(root, base_prefix=base_prefix)
        rng = _system_node_range(root, base_prefix) if base_prefix else None
        n_offset = rng[0] if rng is not None else 0
        for t_idx, per_node in cum_inv.get("gen", {}).items():
            label = tech_cfg.get(t_idx, f"Tech {t_idx}")
            tech_type = _label_and_color(label)
            arr = np.zeros(n_nodes)
            for n_idx, mw in per_node.items():
                local_idx = n_idx - n_offset
                if 0 <= local_idx < n_nodes:
                    arr[local_idx] += mw
            _add(tech_type, arr)

        # Build pie segments
        node_data: dict[int, list[dict]] = {}
        for tech_type, caps in tech_caps.items():
            color = (label_color.get(tech_type)
                     or gen_colors.get(tech_type, default_color))
            for ni in range(min(n_nodes, len(caps))):
                val = float(caps[ni])
                if val > 0.01:
                    node_data.setdefault(ni, []).append({
                        "value": round(val, 2),
                        "color": color,
                        "label": tech_type,
                    })

        self._render_pie_data(node_data, n_nodes, coords, title, year)

    def _render_re_composition_pies(self, scenario: Any, root: Any,
                                     title: str, year: int,
                                     base_prefix: str = ""):
        if "generation" not in scenario:
            return

        coords = self._get_coords()
        if not coords:
            return

        resolve, _ = self._tech_resolver(root, base_prefix)
        gen_datasets = _flat_datasets(scenario["generation"])
        if not gen_datasets:
            return

        n_nodes = min(len(coords), max(
            ds[:].shape[0] for ds in gen_datasets.values()
        ))

        re_color = "#22c55e"
        nonre_color = "#6b7280"

        node_data: dict[int, list[dict]] = {}
        re_total = np.zeros(n_nodes)
        nonre_total = np.zeros(n_nodes)

        for gn, ds in gen_datasets.items():
            data = ds[:]
            gen_sum = np.atleast_1d(data.sum(axis=-1))
            # Renewable flag from the technology table's ``type``
            # (renewable category) rather than a name guess.
            info = resolve(gn)
            if info is not None:
                is_re = info.get("category") in ("renewable", "rooftop")
            else:
                is_re = self._is_type_renewable(self._extract_fuel_from_name(gn))
            for ni in range(min(n_nodes, len(gen_sum))):
                if is_re:
                    re_total[ni] += gen_sum[ni]
                else:
                    nonre_total[ni] += gen_sum[ni]

        for ni in range(n_nodes):
            segs = []
            if re_total[ni] > 0.01:
                segs.append({"value": round(float(re_total[ni]), 2),
                             "color": re_color, "label": "Renewable"})
            if nonre_total[ni] > 0.01:
                segs.append({"value": round(float(nonre_total[ni]), 2),
                             "color": nonre_color, "label": "Non-Renewable"})
            if segs:
                node_data[ni] = segs

        self._render_pie_data(node_data, n_nodes, coords, title, year)

    def _render_pie_data(self, node_data: dict[int, list[dict]],
                         n_nodes: int, coords: list[tuple[float, float]],
                         title: str, year: int):
        totals = [
            sum(s["value"] for s in node_data.get(i, []))
            for i in range(n_nodes)
        ]
        max_total = max(totals) if totals else 1.0
        if max_total <= 0:
            max_total = 1.0

        pies = []
        for i in range(n_nodes):
            segs = node_data.get(i, [])
            if not segs:
                continue
            lat, lng = coords[i]
            sz = 25 + 35 * (totals[i] / max_total)
            pies.append({
                "lat": lat,
                "lng": lng,
                "segments": segs,
                "size": round(sz),
                "title": f"Node {i} — {year}",
            })

        if not self._accumulate:
            self._map_widget.clear_results_nodes()
        self._map_widget.add_results_pie_charts(pies)

        seen: dict[str, str] = {}
        for pie in pies:
            for s in pie["segments"]:
                if s["label"] not in seen:
                    seen[s["label"]] = s["color"]

        legend_entries = [
            {"label": lbl, "color": clr}
            for lbl, clr in seen.items()
        ]
        self._map_widget.add_results_pie_legend(
            f"{title} — {year}", legend_entries,
        )

    @staticmethod
    def _load_node_connections(root: Any, base_prefix: str, n: int):
        """Return the n×n transmission-capacity matrix from
        ``system_configuration/nodes/nodes_connections`` (flattened
        square array in the HDF5), or ``None`` if unavailable. Off-
        diagonal entry [i][j] > 0 means a real line joins nodes i and j.
        """
        if root is None:
            return None
        sc = _open_system_config(root, base_prefix)
        try:
            if sc is None or "nodes" not in sc:
                return None
            ng = sc["nodes"]
            if "nodes_connections" not in ng:
                return None
            raw = np.asarray(ng["nodes_connections"][:], dtype=float)
            size = int(round(len(raw) ** 0.5))
            if size * size != len(raw) or size < n:
                # Shape doesn't match the node count — don't risk
                # mis-gating; fall back to drawing every flow.
                return None
            return raw.reshape(size, size)
        except Exception:
            return None

    def _render_flow_lines(self, scenario: Any, hdf5_key: str, title: str,
                           root: Any = None, base_prefix: str = ""):
        if hdf5_key not in scenario:
            import sys
            print(f"[Results] '{hdf5_key}' not found in scenario", file=sys.stderr)
            return

        data = scenario[hdf5_key][:]
        if data.ndim != 3:
            import sys
            print(f"[Results] '{hdf5_key}' has shape {data.shape}, expected 3D", file=sys.stderr)
            return

        coords = self._get_coords()
        if not coords:
            return

        avg_flow = data.mean(axis=2)
        n = min(avg_flow.shape[0], len(coords))

        # Real transmission topology — only node pairs joined by an actual
        # line should ever get a flow arrow. The solver's power_flow
        # matrix can carry spurious off-diagonal values (e.g. the
        # isolated IslaJuventud node shows non-zero "outflow" rows), so
        # without this gate the map invents lines to unconnected nodes.
        conn = self._load_node_connections(root, base_prefix, n)

        max_flow = float(np.abs(avg_flow[:n, :n]).max()) if n > 0 else 1.0
        if max_flow <= 0:
            max_flow = 1.0

        from esfex.visualization.theme import get_heatmap_gradient
        pf_min, pf_max = get_heatmap_gradient("Power Flow")

        lines = []
        for i in range(n):
            for j in range(i + 1, n):
                # Skip pairs with no physical transmission line.
                if conn is not None and conn[i][j] <= 0 and conn[j][i] <= 0:
                    continue

                net = float(avg_flow[i, j] - avg_flow[j, i])
                mag = abs(net)
                if mag < 0.01:
                    continue

                lat_i, lng_i = coords[i]
                lat_j, lng_j = coords[j]

                if net >= 0:
                    c = [[lat_i, lng_i], [lat_j, lng_j]]
                    lbl = f"{i}\u2192{j}"
                else:
                    c = [[lat_j, lng_j], [lat_i, lng_i]]
                    lbl = f"{j}\u2192{i}"

                weight = 2 + 8 * mag / max_flow
                color = _color_interp(mag, 0, max_flow, pf_min, pf_max)
                lines.append({
                    "coords": c,
                    "weight": round(weight, 1),
                    "color": color,
                    "label": lbl,
                    "value": round(mag, 1),
                })

        if not self._accumulate:
            self._map_widget.clear_results_flows()
        self._map_widget.add_results_flow_lines(lines)
        self._map_widget.add_results_legend(
            title, 0.0, max_flow, pf_min, pf_max,
        )

    def _render_fuel_flow_lines(self, scenario: Any, root: Any, title: str):
        if "primary_energy" not in scenario:
            import sys
            print("[Results] 'primary_energy' not found in scenario", file=sys.stderr)
            return
        pe_grp = scenario["primary_energy"]
        if "transport_flows" not in pe_grp:
            import sys
            print("[Results] 'transport_flows' not found in primary_energy", file=sys.stderr)
            return

        coords = self._get_coords()
        if not coords:
            return

        route_geoms: list[list[list[float]]] = []
        route_meta: list[dict] = []
        if "system_configuration" in root and "fuel_routes" in root["system_configuration"]:
            fr_grp = root["system_configuration/fuel_routes"]
            n_routes = int(fr_grp.attrs.get("num_routes", 0))
            for r_idx in range(n_routes):
                rk = f"route_{r_idx}"
                if rk not in fr_grp:
                    route_geoms.append([])
                    route_meta.append({})
                    continue
                rg = fr_grp[rk]
                from_node = int(rg.attrs.get("from_node", 0))
                to_node = int(rg.attrs.get("to_node", 0))
                route_meta.append({"from": from_node, "to": to_node})
                polyline: list[list[float]] = []
                if from_node < len(coords):
                    polyline.append(list(coords[from_node]))
                if "waypoints" in rg:
                    wps = rg["waypoints"][:]
                    for wp in wps:
                        polyline.append([float(wp[0]), float(wp[1])])
                if to_node < len(coords):
                    polyline.append(list(coords[to_node]))
                route_geoms.append(polyline)
        else:
            return

        tf_grp = pe_grp["transport_flows"]
        n_routes = len(route_geoms)
        route_flow = np.zeros(n_routes)
        for fuel_name in tf_grp:
            data = tf_grp[fuel_name][:]
            per_route = data.sum(axis=-1) if data.ndim > 1 else data
            n = min(n_routes, len(per_route))
            route_flow[:n] += per_route[:n]

        max_flow = float(route_flow.max()) if n_routes > 0 else 1.0
        if max_flow <= 0:
            max_flow = 1.0

        from esfex.visualization.theme import get_heatmap_gradient
        fl_min, fl_max = get_heatmap_gradient("Fuel Transport Flow")

        lines = []
        for r_idx in range(n_routes):
            mag = float(route_flow[r_idx])
            if mag < 0.01 or not route_geoms[r_idx]:
                continue
            weight = 2 + 8 * mag / max_flow
            color = _color_interp(mag, 0, max_flow, fl_min, fl_max)
            meta = route_meta[r_idx] if r_idx < len(route_meta) else {}
            lbl = f"Route {meta.get('from', '?')}\u2192{meta.get('to', '?')}"
            lines.append({
                "coords": route_geoms[r_idx],
                "weight": round(weight, 1),
                "color": color,
                "label": lbl,
                "value": round(mag, 1),
            })

        if not self._accumulate:
            self._map_widget.clear_results_flows()
        self._map_widget.add_results_flow_lines(lines)
        self._map_widget.add_results_legend(
            title, 0.0, max_flow, fl_min, fl_max,
        )

    def _render_node_circles(self, values: np.ndarray, title: str):
        coords = self._get_coords()
        if not coords:
            return

        values = np.atleast_1d(values)
        n_nodes = min(len(values), len(coords))
        min_val = float(np.min(values[:n_nodes]))
        max_val = float(np.max(values[:n_nodes]))

        from esfex.visualization.theme import get_heatmap_gradient
        # Use the variable's own title (passed in) — with multi-select
        # the combo no longer has a single "current" text.
        color_min, color_max = get_heatmap_gradient(title)

        circles = []
        for i in range(n_nodes):
            lat, lng = coords[i]
            val = float(values[i])
            if max_val > min_val:
                r = 5 + 25 * (val - min_val) / (max_val - min_val)
            else:
                r = 15
            color = _color_interp(val, min_val, max_val, color_min, color_max)
            circles.append({
                "lat": lat, "lng": lng,
                "radius": r, "color": color,
                "label": f"Node {i}", "value": round(val, 2),
            })

        if not self._accumulate:
            self._map_widget.clear_results_nodes()
        self._map_widget.add_results_node_circles(circles)
        self._map_widget.add_results_legend(
            title, min_val, max_val, color_min, color_max,
        )
