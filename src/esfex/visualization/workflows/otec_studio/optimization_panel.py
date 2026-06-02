# -*- coding: utf-8 -*-
"""OTEC Studio — Optimization panel (M1).

Exposes OTEX's inverse-design optimizer: pick a site, set design bounds and
optional UserConstraints (a CAPEX/AEP/power cap forces an *interior* optimum
instead of degenerating to max power), then minimize LCOE. The LCOE-surface
explorer sweeps two design variables around the optimum so the user can see
*why* it sits where it does.

Reads the active scenario's config (cycle/fluid/cost); writes the resulting
``SiteContext`` and ``OptimizationResult`` back to the scenario.
"""

from __future__ import annotations

import logging

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from esfex.visualization.workflows.otec_studio import optimize as opt
from esfex.visualization.workflows.otec_studio.workers import (
    OptimizeWorker,
    SurfaceWorker,
)

logger = logging.getLogger(__name__)

_VAR_LABELS = {
    "p_gross": "Gross power (MW)",
    "dT_WW": "ΔT warm (K)",
    "dT_CW": "ΔT cold (K)",
    "depth_CW": "CW depth (m)",
}


def _spin(lo, hi, val, decimals=2, step=1.0, suffix=""):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    return s


class OptimizationPanel(QWidget):
    """Inverse-design optimization over a single site."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project = None
        self._site = None
        self._result = None
        self._worker = None
        self._surf_worker = None
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # ── Left: inputs ──
        left = QWidget()
        left_lay = QVBoxLayout(left)

        site_grp = QGroupBox("Site")
        sf = QFormLayout(site_grp)
        self._t_ww = _spin(0, 40, 26.0, 1, 0.5, " °C")
        self._t_cw = _spin(0, 40, 5.0, 1, 0.5, " °C")
        self._dist = _spin(0, 1000, 20.0, 1, 1.0, " km")
        self._lat = _spin(-60, 60, 21.0, 3, 0.1)
        self._lon = _spin(-180, 180, -158.0, 3, 0.1)
        sf.addRow("Warm-water T:", self._t_ww)
        sf.addRow("Cold-water T:", self._t_cw)
        sf.addRow("Distance to shore:", self._dist)
        sf.addRow("Latitude:", self._lat)
        sf.addRow("Longitude:", self._lon)
        left_lay.addWidget(site_grp)

        bounds_grp = QGroupBox("Design bounds")
        bf = QFormLayout(bounds_grp)
        # power shown as positive MW magnitude (OTEX stores negative kW)
        self._pg_min = _spin(1, 500, 1.0, 0, 1.0, " MW")
        self._pg_max = _spin(1, 500, 500.0, 0, 1.0, " MW")
        self._dtw_min = _spin(0.5, 10, 1.0, 1, 0.5, " K")
        self._dtw_max = _spin(0.5, 10, 6.0, 1, 0.5, " K")
        self._dtc_min = _spin(0.5, 10, 1.0, 1, 0.5, " K")
        self._dtc_max = _spin(0.5, 10, 6.0, 1, 0.5, " K")
        self._dep_min = _spin(100, 5000, 600.0, 0, 50.0, " m")
        self._dep_max = _spin(100, 5000, 3000.0, 0, 50.0, " m")
        bf.addRow("Power min / max:", self._row(self._pg_min, self._pg_max))
        bf.addRow("ΔT warm min / max:", self._row(self._dtw_min, self._dtw_max))
        bf.addRow("ΔT cold min / max:", self._row(self._dtc_min, self._dtc_max))
        bf.addRow("CW depth min / max:", self._row(self._dep_min, self._dep_max))
        left_lay.addWidget(bounds_grp)

        con_grp = QGroupBox("Constraints (cap → interior optimum)")
        cf = QFormLayout(con_grp)
        self._con_capex = self._opt_constraint(0, 50000, 300.0, " M$")
        self._con_pnet = self._opt_constraint(0, 1000, 100.0, " MW")
        self._con_aep = self._opt_constraint(0, 1e7, 5e5, " MWh")
        self._con_pgross = self._opt_constraint(0, 1000, 200.0, " MW")
        self._con_paras = self._opt_constraint(0, 1, 0.4, "")
        cf.addRow("Max CAPEX:", self._con_capex[2])
        cf.addRow("Max net power:", self._con_pnet[2])
        cf.addRow("Max AEP:", self._con_aep[2])
        cf.addRow("Max gross power:", self._con_pgross[2])
        cf.addRow("Max parasitic ratio:", self._con_paras[2])
        hint = QLabel(
            "Without any cap, LCOE falls monotonically with size → the optimum "
            "degenerates to the max-power bound. Enable a cap to find the real "
            "cost/size sweet spot."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 10px;")
        cf.addRow(hint)
        left_lay.addWidget(con_grp)

        self._btn_opt = QPushButton("Optimize (minimize LCOE)")
        self._btn_opt.setStyleSheet(
            "background-color: #2980b9; color: white; font-weight: bold; "
            "padding: 8px;"
        )
        self._btn_opt.clicked.connect(self._run_optimize)
        left_lay.addWidget(self._btn_opt)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        left_lay.addWidget(self._progress)
        self._status = QLabel("")
        left_lay.addWidget(self._status)
        left_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(left)
        splitter.addWidget(scroll)

        # ── Right: results + surface ──
        right = QWidget()
        right_lay = QVBoxLayout(right)

        res_grp = QGroupBox("Optimal design")
        self._res_form = QFormLayout(res_grp)
        self._res_labels = {}
        for key, label in [
            ("status", "Status"), ("lcoe", "LCOE"), ("p_net", "Net power"),
            ("capex", "CAPEX"), ("pg", "Gross power"), ("dtw", "ΔT warm"),
            ("dtc", "ΔT cold"), ("depth", "CW depth"), ("viol", "Max violation"),
        ]:
            lbl = QLabel("—")
            self._res_labels[key] = lbl
            self._res_form.addRow(f"{label}:", lbl)
        right_lay.addWidget(res_grp)

        surf_grp = QGroupBox("LCOE surface")
        surf_lay = QVBoxLayout(surf_grp)
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("X:"))
        self._var_x = QComboBox()
        self._var_x.addItems(list(_VAR_LABELS.values()))
        self._var_x.setCurrentIndex(0)  # p_gross
        ctrl.addWidget(self._var_x)
        ctrl.addWidget(QLabel("Y:"))
        self._var_y = QComboBox()
        self._var_y.addItems(list(_VAR_LABELS.values()))
        self._var_y.setCurrentIndex(3)  # depth_CW
        ctrl.addWidget(self._var_y)
        self._btn_surf = QPushButton("Compute surface")
        self._btn_surf.setEnabled(False)
        self._btn_surf.clicked.connect(self._compute_surface)
        ctrl.addWidget(self._btn_surf)
        ctrl.addStretch()
        surf_lay.addLayout(ctrl)
        self._fig = Figure(figsize=(5, 4), dpi=100, layout="constrained")
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._ax = self._fig.add_subplot(111)
        surf_lay.addWidget(self._canvas)
        right_lay.addWidget(surf_grp, 1)

        splitter.addWidget(right)
        splitter.setSizes([380, 620])

    @staticmethod
    def _row(a, b):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(a)
        lay.addWidget(b)
        return w

    @staticmethod
    def _opt_constraint(lo, hi, val, suffix):
        """A (checkbox, spinbox, container) optional-constraint row."""
        cb = QCheckBox()
        sp = _spin(lo, hi, val, 2 if hi <= 1 else 0, 1.0, suffix)
        sp.setEnabled(False)
        cb.toggled.connect(sp.setEnabled)
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(cb)
        lay.addWidget(sp, 1)
        return (cb, sp, w)

    # ------------------------------------------------------------------
    # Scenario sync (called by the window when the active scenario changes)
    # ------------------------------------------------------------------

    def on_scenario_changed(self, scenario, project):
        self._project = project
        res = getattr(project, "resource", None)
        if res is not None:
            if getattr(res, "t_ww", None) is not None:
                self._t_ww.setValue(res.t_ww)
            if getattr(res, "t_cw", None) is not None:
                self._t_cw.setValue(res.t_cw)
            if getattr(res, "latitude", None) is not None:
                self._lat.setValue(res.latitude)
            if getattr(res, "longitude", None) is not None:
                self._lon.setValue(res.longitude)

    # ------------------------------------------------------------------
    # Optimization
    # ------------------------------------------------------------------

    def _collect_bounds(self):
        # MW magnitude → negative kW; (low=more negative=bigger, high=less)
        pg = (-self._pg_max.value() * 1000.0, -self._pg_min.value() * 1000.0)
        return opt.make_bounds(
            p_gross=pg,
            dT_WW=(self._dtw_min.value(), self._dtw_max.value()),
            dT_CW=(self._dtc_min.value(), self._dtc_max.value()),
            depth_CW=(self._dep_min.value(), self._dep_max.value()),
        )

    def _collect_constraints(self):
        def v(pair):
            cb, sp, _ = pair
            return sp.value() if cb.isChecked() else None
        return opt.make_constraints(
            max_capex_MUSD=v(self._con_capex),
            max_p_net_MW=v(self._con_pnet),
            max_aep_MWh=v(self._con_aep),
            max_p_gross_MW=v(self._con_pgross),
            max_parasitic_ratio=v(self._con_paras),
        )

    def _config(self):
        if self._project is not None:
            return self._project.active.config
        from esfex.visualization.workflows.otec_studio.project import StudioConfig
        return StudioConfig()

    def _run_optimize(self):
        self._site = opt.build_site_context(
            self._config(),
            t_ww=self._t_ww.value(), t_cw=self._t_cw.value(),
            dist_shore=self._dist.value(),
            latitude=self._lat.value(), longitude=self._lon.value(),
        )
        self._btn_opt.setEnabled(False)
        self._progress.setVisible(True)
        self._status.setText("Optimizing…")
        self._worker = OptimizeWorker(
            self._site, self._collect_bounds(), self._collect_constraints(),
        )
        self._worker.finished.connect(self._on_optimized)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_optimized(self, res):
        self._result = res
        self._progress.setVisible(False)
        self._btn_opt.setEnabled(True)
        self._btn_surf.setEnabled(True)
        ok = bool(getattr(res, "success", False))
        self._status.setText("Done." if ok else "Did not converge.")
        self._status.setStyleSheet(
            "color: #2ecc71;" if ok else "color: #e67e22;"
        )
        L = self._res_labels
        L["status"].setText(f"{'✓ success' if ok else '✗'} ({res.message})")
        L["lcoe"].setText(f"{res.lcoe:.4f}")
        L["p_net"].setText(f"{abs(res.p_net) / 1000:,.1f} MW")
        L["capex"].setText(f"${res.capex_total / 1e6:,.1f} M")
        L["pg"].setText(f"{abs(res.x.p_gross) / 1000:,.1f} MW")
        L["dtw"].setText(f"{res.x.dT_WW:.2f} K")
        L["dtc"].setText(f"{res.x.dT_CW:.2f} K")
        L["depth"].setText(f"{res.x.depth_CW:,.0f} m")
        L["viol"].setText(f"{res.max_violation:.4g}")
        # persist into the active scenario
        if self._project is not None:
            self._project.active.site = self._site
            self._project.active.design = res

    def _on_error(self, msg):
        self._progress.setVisible(False)
        self._btn_opt.setEnabled(True)
        self._status.setText(f"Error: {msg}")
        self._status.setStyleSheet("color: #e74c3c;")

    # ------------------------------------------------------------------
    # LCOE surface
    # ------------------------------------------------------------------

    def _var_key(self, combo):
        return list(_VAR_LABELS.keys())[combo.currentIndex()]

    def _compute_surface(self):
        if self._site is None or self._result is None:
            return
        var_x = self._var_key(self._var_x)
        var_y = self._var_key(self._var_y)
        if var_x == var_y:
            self._status.setText("Choose two different axes for the surface.")
            return
        x = self._result.x
        base = {
            "p_gross": x.p_gross, "dT_WW": x.dT_WW,
            "dT_CW": x.dT_CW, "depth_CW": x.depth_CW,
        }
        x_vals = self._axis_values(var_x)
        y_vals = self._axis_values(var_y)
        self._btn_surf.setEnabled(False)
        self._progress.setVisible(True)
        self._status.setText("Sweeping LCOE surface…")
        self._surf_worker = SurfaceWorker(
            self._site, base, var_x, var_y, x_vals, y_vals,
        )
        self._surf_worker.finished.connect(self._on_surface)
        self._surf_worker.error.connect(self._on_error)
        self._surf_worker.start()

    def _axis_values(self, var, n=13):
        ranges = {
            "p_gross": (-self._pg_max.value() * 1000, -self._pg_min.value() * 1000),
            "dT_WW": (self._dtw_min.value(), self._dtw_max.value()),
            "dT_CW": (self._dtc_min.value(), self._dtc_max.value()),
            "depth_CW": (self._dep_min.value(), self._dep_max.value()),
        }
        lo, hi = ranges[var]
        return list(np.linspace(lo, hi, n))

    def _on_surface(self, surf):
        self._progress.setVisible(False)
        self._btn_surf.setEnabled(True)
        self._status.setText("Surface ready.")
        self._status.setStyleSheet("color: #2ecc71;")
        self._ax.clear()
        xv = np.array(surf["x_vals"])
        yv = np.array(surf["y_vals"])
        grid = surf["lcoe"]
        # display power axis as positive MW magnitude
        xd = -xv / 1000 if surf["var_x"] == "p_gross" else xv
        yd = -yv / 1000 if surf["var_y"] == "p_gross" else yv
        mesh = self._ax.pcolormesh(xd, yd, grid, shading="auto", cmap="viridis")
        self._fig.colorbar(mesh, ax=self._ax, label="LCOE")
        # mark the optimum
        if self._result is not None:
            ox = getattr(self._result.x, surf["var_x"])
            oy = getattr(self._result.x, surf["var_y"])
            oxd = -ox / 1000 if surf["var_x"] == "p_gross" else ox
            oyd = -oy / 1000 if surf["var_y"] == "p_gross" else oy
            self._ax.plot(oxd, oyd, "r*", markersize=16, markeredgecolor="white",
                          label="optimum")
            self._ax.legend(fontsize=8)
        self._ax.set_xlabel(_VAR_LABELS[surf["var_x"]])
        self._ax.set_ylabel(_VAR_LABELS[surf["var_y"]])
        self._ax.set_title("LCOE surface", fontsize=11, fontweight="bold")
        self._canvas.draw()
