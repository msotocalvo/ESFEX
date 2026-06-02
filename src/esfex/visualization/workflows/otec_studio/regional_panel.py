# -*- coding: utf-8 -*-
"""OTEC Studio — Regional Optimization panel (M7).

Batch inverse-design across every feasible site in a region (OTEX
``run_regional_optimization``), with optional region-wide UserConstraints —
e.g. "if we cap CAPEX per site at 300 M$, which sites stay profitable?". Results
show as a site map coloured by LCOE, a portfolio summary, and a table, with
CSV/HDF5 export (matching the Studio's data-export-only scope).

Network: the regional run downloads data and is slow; it runs off-thread with
graceful failure.
"""

from __future__ import annotations

import logging

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.workflows.otec_studio import optimize as opt
from esfex.visualization.workflows.otec_studio import regional as reg
from esfex.visualization.workflows.otec_studio.project import StudioConfig
from esfex.visualization.workflows.otec_studio.workers import RegionalWorker

logger = logging.getLogger(__name__)


class RegionalPanel(QWidget):
    """Region-wide batch optimization with constraint what-ifs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project = None
        self._df = None
        self._worker = None
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)

        ctrl = QGroupBox("Regional batch optimization  —  network, slow")
        cl = QHBoxLayout(ctrl)
        cl.addWidget(QLabel("Region:"))
        self._region = QComboBox()
        self._region.setEditable(True)
        try:
            self._region.addItems(reg.list_regions())
            idx = self._region.findText("Cuba")
            if idx >= 0:
                self._region.setCurrentIndex(idx)
        except Exception:
            self._region.addItems(["Cuba", "Jamaica", "Fiji", "Mauritius"])
        cl.addWidget(self._region, 1)

        cl.addWidget(QLabel("Cap CAPEX:"))
        self._cap_on = QCheckBox()
        cl.addWidget(self._cap_on)
        self._cap_capex = QDoubleSpinBox()
        self._cap_capex.setRange(0, 50000)
        self._cap_capex.setValue(300.0)
        self._cap_capex.setSuffix(" M$")
        self._cap_capex.setEnabled(False)
        self._cap_on.toggled.connect(self._cap_capex.setEnabled)
        cl.addWidget(self._cap_capex)

        self._btn = QPushButton("Run regional")
        self._btn.setStyleSheet(
            "background-color: #2980b9; color: white; font-weight: bold; padding: 6px;")
        self._btn.clicked.connect(self._run)
        cl.addWidget(self._btn)
        root.addWidget(ctrl)

        opts = QHBoxLayout()
        self._feasible_only = QCheckBox("Feasible only")
        self._feasible_only.setChecked(True)
        self._feasible_only.toggled.connect(self._redraw)
        opts.addWidget(self._feasible_only)
        self._btn_export = QPushButton("Export CSV")
        self._btn_export.setEnabled(False)
        self._btn_export.clicked.connect(self._export)
        opts.addWidget(self._btn_export)
        # Ported from OTEC Analysis: cluster feasible sites into dev zones.
        self._btn_zones = QPushButton("Cluster zones")
        self._btn_zones.setToolTip(
            "DBSCAN-cluster feasible sites into development zones (polygons)")
        self._btn_zones.setEnabled(False)
        self._btn_zones.clicked.connect(self._cluster_zones)
        opts.addWidget(self._btn_zones)
        opts.addStretch()
        self._summary = QLabel("Run a region to see results.")
        opts.addWidget(self._summary, 1)
        root.addLayout(opts)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        split = QSplitter(Qt.Orientation.Horizontal)
        self._fig = Figure(figsize=(5, 4), dpi=100, layout="constrained")
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._ax = self._fig.add_subplot(111)
        split.addWidget(self._canvas)

        self._table = QTableWidget()
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        split.addWidget(self._table)
        split.setSizes([520, 480])
        root.addWidget(split, 1)

    # ------------------------------------------------------------------
    # Scenario sync
    # ------------------------------------------------------------------

    def on_scenario_changed(self, scenario, project):
        self._project = project

    def _config(self) -> StudioConfig:
        return self._project.active.config if self._project else StudioConfig()

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _run(self):
        cfg = self._config()
        kwargs = {
            "cost_level": cfg.cost_level,
            "cycle_type": cfg.cycle_type,
            "fluid_type": cfg.fluid_type,
        }
        if self._cap_on.isChecked():
            kwargs["user_constraints"] = opt.make_constraints(
                max_capex_MUSD=self._cap_capex.value())
        self._btn.setEnabled(False)
        self._progress.setVisible(True)
        self._summary.setText("Running regional optimization (network, slow)…")
        self._worker = RegionalWorker(self._region.currentText(), kwargs)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, df):
        self._progress.setVisible(False)
        self._btn.setEnabled(True)
        self._df = df
        has = df is not None and len(df) > 0
        self._btn_export.setEnabled(has)
        self._btn_zones.setEnabled(has)
        self._zones = None
        self._redraw()

    def _on_error(self, msg):
        self._progress.setVisible(False)
        self._btn.setEnabled(True)
        self._summary.setText(
            f"<span style='color:#e67e22;'>Regional run unavailable "
            f"(offline / no credentials): {msg}</span>")

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def _view_frame(self):
        if self._df is None:
            return None
        return reg.filter_feasible(self._df) if self._feasible_only.isChecked() \
            else self._df

    def _redraw(self):
        if self._df is None:
            return
        s = reg.summarize_regional(self._df)
        cap = (f"{s['total_capacity_MW']:,.0f} MW"
               if s["total_capacity_MW"] is not None else "—")
        best = s["best_site"]
        best_txt = (f"best {best['lcoe']:.3f} @({best['latitude']:.1f},"
                    f"{best['longitude']:.1f})" if best else "—")
        self._summary.setText(
            f"<b>{s['n_feasible']}/{s['n_total']}</b> feasible · "
            f"LCOE {s['lcoe_min']:.3f}–{s['lcoe_max']:.3f} (med "
            f"{s['lcoe_median']:.3f}) · capacity {cap} · {best_txt}"
            if s["n_feasible"] else f"0/{s['n_total']} feasible.")
        self._draw_map()
        self._fill_table()

    def _draw_map(self):
        df = self._view_frame()
        ax = self._ax
        ax.clear()
        if df is None or len(df) == 0 or "longitude" not in df.columns:
            self._canvas.draw()
            return
        lon = df["longitude"].to_numpy(dtype=float)
        lat = df["latitude"].to_numpy(dtype=float)
        lcoe = df["lcoe_min"].to_numpy(dtype=float)
        sc = ax.scatter(lon, lat, c=lcoe, cmap="viridis_r", s=45,
                        edgecolors="white", linewidths=0.5, zorder=3)
        self._fig.colorbar(sc, ax=ax, label="LCOE")
        # Development-zone polygons overlaid when clustered.
        zones = getattr(self, "_zones", None)
        if zones is not None and len(zones):
            for geom in zones.geometry:
                try:
                    xs, ys = geom.exterior.xy
                    ax.fill(xs, ys, alpha=0.18, color="#e74c3c", zorder=1)
                    ax.plot(xs, ys, color="#c0392b", lw=1.2, zorder=2)
                except (AttributeError, NotImplementedError):
                    pass  # multipolygon / non-polygon geometry: skip outline
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
        ax.set_title("Optimal LCOE by site", fontsize=11, fontweight="bold")
        ax.grid(alpha=0.3)
        self._canvas.draw()

    def _fill_table(self):
        df = self._view_frame()
        if df is None:
            return
        cols = [c for c in (
            "id", "latitude", "longitude", "lcoe_min", "p_net_kW",
            "depth_CW_opt", "capex_total_MUSD", "feasible") if c in df.columns]
        self._table.setColumnCount(len(cols))
        self._table.setHorizontalHeaderLabels(cols)
        self._table.setRowCount(len(df))
        for r in range(len(df)):
            for c, key in enumerate(cols):
                v = df.iloc[r][key]
                if isinstance(v, (float, np.floating)):
                    text = f"{v:,.3f}" if abs(v) < 1000 else f"{v:,.0f}"
                else:
                    text = str(v)
                self._table.setItem(r, c, QTableWidgetItem(text))

    def _cluster_zones(self):
        if self._df is None:
            return
        from esfex.visualization.workflows.otec_studio import engineering as eng
        s = reg.summarize_regional(self._df)
        thr = (s["lcoe_max"] if s["lcoe_max"] is not None else 1.0)
        try:
            self._zones = eng.zones_from_regional(
                self._df, lcoe_threshold=thr, buffer_km=10.0)
        except Exception as exc:  # noqa: BLE001
            self._summary.setText(
                f"<span style='color:#e67e22;'>Zone clustering failed: "
                f"{exc}</span>")
            return
        n = len(self._zones)
        self._draw_map()  # redraw with zone overlay
        self._summary.setText(
            self._summary.text() + f"  ·  <b>{n} development zone(s)</b>")

    def _export(self):
        if self._df is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export regional results", "otec_regional.csv",
            "CSV (*.csv);;HDF5 (*.h5)")
        if not path:
            return
        try:
            if path.endswith(".h5"):
                self._df.to_hdf(path, key="regional", mode="w")
            else:
                self._df.to_csv(path, index=False)
            QMessageBox.information(self, "Export", f"Saved to {path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(exc))
