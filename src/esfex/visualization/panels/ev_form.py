"""EV configuration form."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import GuiEVCategory, GuiModel
from esfex.visualization.i18n import tr
from esfex.visualization.panels.word_wrap_header import WordWrapHeaderView

# Row labels for the transposed categories table (characteristics as rows)
def _cat_row_labels() -> list[str]:
    return [
        tr("ev_form.battery_kwh"),
        tr("ev_form.charging_kw"),
        tr("ev_form.v2g_power_kw"),
        tr("ev_form.v2g_participation"),
        tr("ev_form.eff_charge"),
        tr("ev_form.eff_discharge"),
        tr("ev_form.min_soc"),
        tr("ev_form.max_adoption"),
        tr("ev_form.growth_rate"),
        tr("ev_form.mid_point"),
    ]

_CAT_FIELDS = [
    "battery_capacity", "charging_power", "v2g_power",
    "v2g_participation", "efficiency_charge", "efficiency_discharge",
    "min_soc", "max_adoption", "growth_rate", "mid_point_fraction",
]


class EVForm(QWidget):
    """Property editor for EV configuration."""

    evConfigChanged = Signal()

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

        # ── Categories (transposed: rows=characteristics, cols=categories) ──
        grp_cat = QGroupBox(tr("ev_form.group_categories"))
        cat_layout = QVBoxLayout(grp_cat)
        cat_layout.setContentsMargins(6, 6, 6, 6)

        btn_row = QHBoxLayout()
        self._new_cat_name = QLineEdit()
        self._new_cat_name.setPlaceholderText(tr("ev_form.category_placeholder"))
        btn_row.addWidget(self._new_cat_name)
        add_btn = QPushButton(tr("ev_form.add_btn"))
        add_btn.clicked.connect(self._on_add_category)
        btn_row.addWidget(add_btn)
        self._remove_cat_btn = QPushButton(tr("ev_form.remove_btn"))
        self._remove_cat_btn.clicked.connect(self._on_remove_category)
        btn_row.addWidget(self._remove_cat_btn)
        cat_layout.addLayout(btn_row)

        self._cat_table = QTableWidget()
        self._cat_table.setHorizontalHeader(WordWrapHeaderView(self._cat_table))
        self._cat_table.setRowCount(len(_cat_row_labels()))
        self._cat_table.setVerticalHeaderLabels(_cat_row_labels())
        self._cat_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._cat_table.cellChanged.connect(self._on_cat_table_changed)
        cat_layout.addWidget(self._cat_table)

        outer.addWidget(grp_cat)

        # ── Initial SOC (per-node) ──
        grp_soc = QGroupBox(tr("ev_form.group_initial_soc"))
        soc_layout = QVBoxLayout(grp_soc)
        soc_layout.setContentsMargins(6, 6, 6, 6)

        self._soc_table = QTableWidget()
        self._soc_table.setHorizontalHeader(WordWrapHeaderView(self._soc_table))
        self._soc_table.setColumnCount(1)
        self._soc_table.setHorizontalHeaderLabels([tr("ev_form.initial_soc")])
        self._soc_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._soc_table.cellChanged.connect(self._on_soc_changed)
        soc_layout.addWidget(self._soc_table)

        outer.addWidget(grp_soc)

        # ── Quantities (per-node × category) ──
        grp_qty = QGroupBox(tr("ev_form.group_quantities"))
        qty_layout = QVBoxLayout(grp_qty)
        qty_layout.setContentsMargins(6, 6, 6, 6)

        self._qty_table = QTableWidget()
        self._qty_table.setHorizontalHeader(WordWrapHeaderView(self._qty_table))
        self._qty_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._qty_table.cellChanged.connect(self._on_qty_changed)
        qty_layout.addWidget(self._qty_table)

        outer.addWidget(grp_qty)

        # ── Base Patterns (24h × category) ──
        grp_pat = QGroupBox(tr("ev_form.group_patterns"))
        pat_layout = QVBoxLayout(grp_pat)
        pat_layout.setContentsMargins(6, 6, 6, 6)

        self._pat_table = QTableWidget()
        self._pat_table.setHorizontalHeader(WordWrapHeaderView(self._pat_table))
        self._pat_table.setRowCount(24)
        self._pat_table.setVerticalHeaderLabels([f"{h:02d}:00" for h in range(24)])
        self._pat_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._pat_table.cellChanged.connect(self._on_pat_changed)
        pat_layout.addWidget(self._pat_table)

        outer.addWidget(grp_pat)

        outer.addStretch()

    def load_element(self, element_id: str = ""):
        """Load EV config. element_id is ignored."""
        self._updating = True
        ev = self._model.state.ev_config
        nodes = self._model.state.nodes

        self._header_label.setText(tr("ev_form.system_label", name=self._model.state.name))

        # Categories table (transposed: rows=fields, cols=categories)
        cat_ids = list(ev.categories.keys())
        self._cat_table.setColumnCount(len(cat_ids))
        self._cat_table.setHorizontalHeaderLabels(cat_ids)
        for col, cid in enumerate(cat_ids):
            cat = ev.categories[cid]
            vals = [getattr(cat, f) for f in _CAT_FIELDS]
            for row, v in enumerate(vals):
                self._cat_table.setItem(row, col, QTableWidgetItem(f"{v}"))

        # SOC table
        num_nodes = len(nodes)
        while len(ev.initial_soc) < num_nodes:
            ev.initial_soc.append(0.5)
        self._soc_table.setRowCount(num_nodes)
        for i in range(num_nodes):
            self._soc_table.setVerticalHeaderItem(
                i, QTableWidgetItem(nodes[i].name)
            )
            self._soc_table.setItem(
                i, 0, QTableWidgetItem(f"{ev.initial_soc[i]}")
            )

        # Quantities table
        self._qty_table.setColumnCount(len(cat_ids))
        self._qty_table.setHorizontalHeaderLabels(cat_ids)
        self._qty_table.setRowCount(num_nodes)
        for i in range(num_nodes):
            self._qty_table.setVerticalHeaderItem(
                i, QTableWidgetItem(nodes[i].name)
            )
            for j, cid in enumerate(cat_ids):
                cat = ev.categories[cid]
                while len(cat.quantity) < num_nodes:
                    cat.quantity.append(0)
                self._qty_table.setItem(
                    i, j, QTableWidgetItem(str(cat.quantity[i]))
                )

        # Patterns table
        self._pat_table.setColumnCount(len(cat_ids))
        self._pat_table.setHorizontalHeaderLabels(cat_ids)
        for j, cid in enumerate(cat_ids):
            cat = ev.categories[cid]
            while len(cat.base_pattern) < 24:
                cat.base_pattern.append(0.0)
            for h in range(24):
                self._pat_table.setItem(
                    h, j, QTableWidgetItem(f"{cat.base_pattern[h]}")
                )

        self._updating = False

    def _on_add_category(self):
        cid = self._new_cat_name.text().strip()
        if not cid or cid in self._model.state.ev_config.categories:
            return
        num_nodes = len(self._model.state.nodes)
        cat = GuiEVCategory(
            category_id=cid,
            quantity=[0] * num_nodes,
            base_pattern=[0.0] * 24,
        )
        self._model.state.ev_config.categories[cid] = cat
        self._new_cat_name.clear()
        self.load_element()
        self.evConfigChanged.emit()

    def _on_remove_category(self):
        col = self._cat_table.currentColumn()
        if col < 0:
            return
        cat_ids = list(self._model.state.ev_config.categories.keys())
        if col >= len(cat_ids):
            return
        cid = cat_ids[col]
        del self._model.state.ev_config.categories[cid]
        self.load_element()
        self.evConfigChanged.emit()

    def _on_cat_table_changed(self, row: int, col: int):
        if self._updating:
            return
        cat_ids = list(self._model.state.ev_config.categories.keys())
        if col >= len(cat_ids):
            return
        cat = self._model.state.ev_config.categories[cat_ids[col]]
        val_item = self._cat_table.item(row, col)
        if not val_item:
            return
        try:
            val = float(val_item.text())
        except ValueError:
            return
        if row < len(_CAT_FIELDS):
            setattr(cat, _CAT_FIELDS[row], val)
            self.evConfigChanged.emit()

    def _on_soc_changed(self, row: int, col: int):
        if self._updating:
            return
        ev = self._model.state.ev_config
        item = self._soc_table.item(row, 0)
        if not item:
            return
        try:
            val = float(item.text())
        except ValueError:
            return
        if row < len(ev.initial_soc):
            ev.initial_soc[row] = val
            self.evConfigChanged.emit()

    def _on_qty_changed(self, row: int, col: int):
        if self._updating:
            return
        cat_ids = list(self._model.state.ev_config.categories.keys())
        if col >= len(cat_ids):
            return
        cat = self._model.state.ev_config.categories[cat_ids[col]]
        item = self._qty_table.item(row, col)
        if not item:
            return
        try:
            val = int(float(item.text()))
        except ValueError:
            return
        if row < len(cat.quantity):
            cat.quantity[row] = val
            self.evConfigChanged.emit()

    def _on_pat_changed(self, row: int, col: int):
        if self._updating:
            return
        cat_ids = list(self._model.state.ev_config.categories.keys())
        if col >= len(cat_ids):
            return
        cat = self._model.state.ev_config.categories[cat_ids[col]]
        item = self._pat_table.item(row, col)
        if not item:
            return
        try:
            val = float(item.text())
        except ValueError:
            return
        if row < len(cat.base_pattern):
            cat.base_pattern[row] = val
            self.evConfigChanged.emit()
