# -*- coding: utf-8 -*-
"""OTEC Studio — Site & Resource panel (M6).

Manage candidate sites and push one into the shared project ResourceData, which
auto-fills the Optimization / Economics / Operation panels (the integration win
the wizard lacks: pick a site once, everywhere reads it). Optionally apply a
CMIP6/SSP climate delta to the design temperatures, and enrich sites with OTEX
siting hazard layers.

Network operations (climate delta, hazard enrichment) run off-thread and degrade
gracefully — the panel stays usable with manual site data if they are
unavailable (offline / no credentials).
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.workflows.otec_studio import resource as rsrc
from esfex.visualization.workflows.otec_studio.workers import (
    ClimateDeltaWorker,
    HazardEnrichWorker,
)

logger = logging.getLogger(__name__)

_COLS = ["name", "longitude", "latitude", "t_ww", "t_cw", "dist_shore"]


class ResourcePanel(QWidget):
    """Site management + climate scenario + hazard enrichment."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project = None
        self._sites = [rsrc.make_site("Site 1", -158.0, 21.0, 26.0, 5.0)]
        self._worker = None
        self._build_ui()
        self._refresh_table()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)

        # ── Site table ──
        tgrp = QGroupBox("Candidate sites")
        tl = QVBoxLayout(tgrp)
        self._table = QTableWidget()
        self._table.setColumnCount(len(_COLS))
        self._table.setHorizontalHeaderLabels(
            ["Name", "Lon", "Lat", "Warm T", "Cold T", "Dist (km)"]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._table.itemSelectionChanged.connect(self._on_select)
        tl.addWidget(self._table)

        btns = QHBoxLayout()
        b_add = QPushButton("Add site"); b_add.clicked.connect(self._add_site)
        btns.addWidget(b_add)
        b_del = QPushButton("Remove"); b_del.clicked.connect(self._remove_site)
        btns.addWidget(b_del)
        b_use = QPushButton("Use as active resource →")
        b_use.setStyleSheet(
            "background-color: #2980b9; color: white; font-weight: bold; padding: 6px;")
        b_use.setToolTip(
            "Push the selected site into the project so Optimization / Economics "
            "/ Operation auto-fill from it")
        b_use.clicked.connect(self._use_as_resource)
        btns.addWidget(b_use)
        btns.addStretch()
        tl.addLayout(btns)
        root.addWidget(tgrp, 1)

        # ── Climate scenario ──
        cgrp = QGroupBox("Climate scenario (CMIP6 / SSP)  —  network")
        cl = QHBoxLayout(cgrp)
        cl.addWidget(QLabel("Scenario:"))
        self._scn = QComboBox(); self._scn.addItems(rsrc.SSP_SCENARIOS)
        self._scn.setCurrentText("ssp245")
        cl.addWidget(self._scn)
        cl.addWidget(QLabel("Year:"))
        self._year = QSpinBox(); self._year.setRange(2030, 2100); self._year.setValue(2050)
        cl.addWidget(self._year)
        cl.addWidget(QLabel("CW depth:"))
        self._depth = QDoubleSpinBox(); self._depth.setRange(100, 2000)
        self._depth.setValue(1000.0); self._depth.setSuffix(" m")
        cl.addWidget(self._depth)
        self._b_clim = QPushButton("Fetch & apply delta")
        self._b_clim.clicked.connect(self._fetch_climate)
        cl.addWidget(self._b_clim)
        cl.addStretch()
        root.addWidget(cgrp)

        # ── Resource characterization (ported from OTEC Analysis) ──
        chgrp = QGroupBox("Resource characterization (seasonal ΔT, Carnot)")
        chl = QVBoxLayout(chgrp)
        crow = QHBoxLayout()
        b_char = QPushButton("Characterize selected site")
        b_char.setToolTip(
            "Monthly ΔT / temperature pattern + Carnot ceiling for the site "
            "(synthetic seasonal series when no CMEMS daily data is attached)")
        b_char.clicked.connect(self._characterize)
        crow.addWidget(b_char)
        self._char_info = QLabel("")
        crow.addWidget(self._char_info, 1)
        chl.addLayout(crow)
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        self._char_fig = Figure(figsize=(5, 2.6), dpi=100, layout="constrained")
        self._char_canvas = FigureCanvasQTAgg(self._char_fig)
        self._char_ax = self._char_fig.add_subplot(111)
        chl.addWidget(self._char_canvas)
        root.addWidget(chgrp)

        # ── Hazard enrichment ──
        hgrp = QGroupBox("Siting hazards (MPA / AIS / seismic / cyclone)  —  network")
        hl = QHBoxLayout(hgrp)
        self._b_haz = QPushButton("Enrich hazards for all sites")
        self._b_haz.clicked.connect(self._enrich)
        hl.addWidget(self._b_haz)
        hl.addStretch()
        root.addWidget(hgrp)

        self._progress = QProgressBar(); self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        root.addWidget(self._progress)
        self._status = QLabel("")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

    # ------------------------------------------------------------------
    # Table <-> sites
    # ------------------------------------------------------------------

    def _refresh_table(self):
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._sites))
        for r, s in enumerate(self._sites):
            for c, key in enumerate(_COLS):
                val = s.get(key, "")
                text = f"{val:g}" if isinstance(val, float) else str(val)
                self._table.setItem(r, c, QTableWidgetItem(text))
        self._table.blockSignals(False)

    def _read_table(self):
        """Pull edits back into the site records."""
        sites = []
        for r in range(self._table.rowCount()):
            def cell(c, default=""):
                it = self._table.item(r, c)
                return it.text() if it else default
            try:
                sites.append(rsrc.make_site(
                    cell(0, f"Site {r+1}"),
                    float(cell(1, 0)), float(cell(2, 0)),
                    float(cell(3, 26)), float(cell(4, 5)),
                    float(cell(5, 20)),
                ))
            except ValueError:
                sites.append(self._sites[r] if r < len(self._sites) else
                             rsrc.make_site(f"Site {r+1}", 0, 0, 26, 5))
        self._sites = sites
        return sites

    def _selected_row(self):
        rows = self._table.selectionModel().selectedRows()
        if rows:
            return rows[0].row()
        return self._table.currentRow() if self._table.currentRow() >= 0 else 0

    def _on_select(self):
        pass

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _add_site(self):
        self._read_table()
        n = len(self._sites) + 1
        self._sites.append(rsrc.make_site(f"Site {n}", -158.0, 21.0, 26.0, 5.0))
        self._refresh_table()

    def _remove_site(self):
        self._read_table()
        if len(self._sites) <= 1:
            return
        r = self._selected_row()
        self._sites.pop(r)
        self._refresh_table()

    def _use_as_resource(self):
        self._read_table()
        r = self._selected_row()
        site = self._sites[r]
        if self._project is not None:
            self._project.resource = rsrc.site_to_resource(site)
            # notify sibling panels so they auto-fill
            window = self.window()
            if hasattr(window, "_sync_panels"):
                window._sync_panels()
        self._status.setText(
            f"Active resource set to '{site['name']}' "
            f"(T_WW={site['t_ww']:g}°C, T_CW={site['t_cw']:g}°C) — "
            f"other panels now read this site."
        )
        self._status.setStyleSheet("color: #2ecc71;")

    def _characterize(self):
        import numpy as np
        from esfex.visualization.workflows.otec_studio import engineering as eng
        self._read_table()
        s = self._sites[self._selected_row()]
        daily = eng.synthetic_daily(s["t_ww"], s["t_cw"])
        mc = eng.monthly_characterization(daily)
        self._char_info.setText(
            f"ΔT {mc['dt_min']:.1f}–{mc['dt_max']:.1f} K · "
            f"Carnot {mc['carnot_mean'] * 100:.2f}%")
        ax = self._char_ax
        ax.clear()
        m = mc["months"]
        ax.plot(m, mc["mean_warm"], "o-", color="#e67e22", ms=3, label="warm")
        ax.plot(m, mc["mean_cold"], "o-", color="#3498db", ms=3, label="cold")
        ax2 = ax.twinx()
        ax2.bar(m, mc["mean_dt"], alpha=0.2, color="#2ecc71")
        ax2.set_ylabel("ΔT (K)")
        ax.set_xlabel("Month"); ax.set_ylabel("Temperature (°C)")
        ax.set_title("Seasonal resource", fontsize=10, fontweight="bold")
        ax.legend(fontsize=7, loc="center left")
        ax.set_xticks(list(m))
        self._char_canvas.draw()

    def on_scenario_changed(self, scenario, project):
        self._project = project

    # ── climate (network) ──
    def _fetch_climate(self):
        self._read_table()
        r = self._selected_row()
        site = self._sites[r]
        self._b_clim.setEnabled(False)
        self._progress.setVisible(True)
        self._status.setText(
            f"Fetching {self._scn.currentText()} delta @ {self._year.value()} "
            f"(network)…")
        self._status.setStyleSheet("color: #888;")
        self._clim_row = r
        self._worker = ClimateDeltaWorker(
            self._scn.currentText(), self._year.value(),
            site["longitude"], site["latitude"], self._depth.value(),
        )
        self._worker.finished.connect(self._on_climate)
        self._worker.error.connect(self._on_net_error)
        self._worker.start()

    def _on_climate(self, out):
        self._progress.setVisible(False)
        self._b_clim.setEnabled(True)
        delta = out.get("delta_mean", 0.0)
        label = f"{self._scn.currentText()}@{self._year.value()}"
        # warm-water surface delta; cold-water (deep) delta assumed smaller
        new = rsrc.apply_climate_delta(
            self._sites[self._clim_row], delta_ww=delta, delta_cw=delta * 0.3,
            label=label,
        )
        self._sites.append(new)
        self._refresh_table()
        self._status.setText(
            f"Applied Δ={delta:+.2f}°C ({label}) → added shifted site "
            f"'{new['name']}'.")
        self._status.setStyleSheet("color: #2ecc71;")

    # ── hazards (network) ──
    def _enrich(self):
        sites = self._read_table()
        self._b_haz.setEnabled(False)
        self._progress.setVisible(True)
        self._status.setText("Enriching siting hazard layers (network)…")
        self._status.setStyleSheet("color: #888;")
        self._worker = HazardEnrichWorker(sites)
        self._worker.finished.connect(self._on_hazards)
        self._worker.error.connect(self._on_net_error)
        self._worker.start()

    def _on_hazards(self, df):
        self._progress.setVisible(False)
        self._b_haz.setEnabled(True)
        present = [c for c in rsrc.HAZARD_COLUMNS if c in df.columns]
        # append hazard columns to the table
        base = len(_COLS)
        self._table.setColumnCount(base + len(present))
        for j, c in enumerate(present):
            self._table.setHorizontalHeaderItem(
                base + j, QTableWidgetItem(c))
            for r in range(min(len(df), self._table.rowCount())):
                val = df.iloc[r][c]
                text = f"{val:g}" if isinstance(val, float) else str(val)
                self._table.setItem(r, base + j, QTableWidgetItem(text))
        self._status.setText(f"Enriched: {', '.join(present)}.")
        self._status.setStyleSheet("color: #2ecc71;")

    def _on_net_error(self, msg):
        self._progress.setVisible(False)
        self._b_clim.setEnabled(True)
        self._b_haz.setEnabled(True)
        self._status.setText(
            f"Network operation unavailable (offline / no credentials): {msg}")
        self._status.setStyleSheet("color: #e67e22;")
