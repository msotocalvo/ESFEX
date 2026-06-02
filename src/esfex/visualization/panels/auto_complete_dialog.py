"""Auto-complete network preview dialog.

Shows a table of proposed connections from isolated buses to the main
network.  The user can select/deselect individual connections before
applying them.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from esfex.visualization.data.auto_complete import ConnectionPlan
from esfex.visualization.i18n import tr


class AutoCompleteDialog(QDialog):
    """Preview dialog for auto-complete network proposals."""

    def __init__(
        self, plans: list[ConnectionPlan], parent=None,
    ) -> None:
        super().__init__(parent)
        self._plans = plans
        self.setWindowTitle(tr("auto_complete.title"))
        self.setMinimumSize(700, 400)
        self.resize(850, 480)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Summary label
        n = len(self._plans)
        total_equip = sum(len(p.equipment_ids) for p in self._plans)
        summary = tr("auto_complete.summary").replace(
            "{n}", str(total_equip),
        ).replace("{g}", str(n))
        lbl = QLabel(summary)
        lbl.setStyleSheet("font-size: 13px; margin: 6px 0;")
        layout.addWidget(lbl)

        # Table
        cols = [
            tr("auto_complete.col_select"),
            tr("auto_complete.col_from"),
            tr("auto_complete.col_equipment"),
            tr("auto_complete.col_reason"),
            tr("auto_complete.col_target"),
            tr("auto_complete.col_distance"),
            tr("auto_complete.col_tr_capacity"),
            tr("auto_complete.col_line_capacity"),
        ]
        self._table = QTableWidget(len(self._plans), len(cols))
        self._table.setHorizontalHeaderLabels(cols)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)

        for row, plan in enumerate(self._plans):
            # Checkbox column
            chk = QTableWidgetItem()
            chk.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
            )
            chk.setCheckState(
                Qt.CheckState.Checked if plan.selected
                else Qt.CheckState.Unchecked
            )
            self._table.setItem(row, 0, chk)

            self._set_text(row, 1, plan.isolated_bus_id)
            self._set_text(row, 2, plan.equipment_summary)
            reason_label = tr(f"auto_complete.reason_{plan.reason}")
            self._set_text(row, 3, reason_label)
            self._set_text(row, 4, plan.target_bus_id)
            self._set_text(row, 5, f"{plan.distance_km:.1f}")
            self._set_text(row, 6, f"{plan.transformer_capacity_mva:.1f}")
            self._set_text(row, 7, f"{plan.line_capacity_mw:.1f}")

        layout.addWidget(self._table, 1)

        # Info label
        info = QLabel(tr("auto_complete.info"))
        info.setStyleSheet("color: #888; font-size: 11px; margin: 4px 0;")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Buttons
        btn_layout = QHBoxLayout()

        btn_all = QPushButton(tr("auto_complete.select_all"))
        btn_all.clicked.connect(self._select_all)
        btn_layout.addWidget(btn_all)

        btn_none = QPushButton(tr("auto_complete.deselect_all"))
        btn_none.clicked.connect(self._deselect_all)
        btn_layout.addWidget(btn_none)

        btn_layout.addStretch()

        btn_cancel = QPushButton(tr("common.cancel"))
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_apply = QPushButton(tr("auto_complete.apply"))
        btn_apply.setDefault(True)
        btn_apply.clicked.connect(self.accept)
        btn_layout.addWidget(btn_apply)

        layout.addLayout(btn_layout)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_text(self, row: int, col: int, text: str):
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self._table.setItem(row, col, item)

    def _select_all(self):
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item:
                item.setCheckState(Qt.CheckState.Checked)

    def _deselect_all(self):
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked)

    def get_selected_plans(self) -> list[ConnectionPlan]:
        """Return plans with selection state updated from checkboxes."""
        for row, plan in enumerate(self._plans):
            item = self._table.item(row, 0)
            plan.selected = (
                item is not None
                and item.checkState() == Qt.CheckState.Checked
            )
        return [p for p in self._plans if p.selected]
