# -*- coding: utf-8 -*-
"""Background workers for OTEC Studio (keep OTEX calls off the GUI thread)."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QThread, Signal

from esfex.visualization.workflows.otec_studio import economics as eco
from esfex.visualization.workflows.otec_studio import operation as oper
from esfex.visualization.workflows.otec_studio import optimize as opt
from esfex.visualization.workflows.otec_studio import regional as _reg
from esfex.visualization.workflows.otec_studio import resource as _rsrc
from esfex.visualization.workflows.otec_studio import uq as _uq


class OptimizeWorker(QThread):
    """Run ``optimize_site`` off-thread → emits OptimizationResult."""

    finished = Signal(object)
    error = Signal(str)

    def __init__(self, site: Any, bounds: Any, constraints: Optional[Any]):
        super().__init__()
        self._site = site
        self._bounds = bounds
        self._constraints = constraints

    def run(self):
        try:
            res = opt.run_optimization(
                self._site, bounds=self._bounds, constraints=self._constraints,
            )
            self.finished.emit(res)
        except Exception as exc:  # noqa: BLE001 — surface to the UI
            self.error.emit(str(exc))


class SurfaceWorker(QThread):
    """Sweep the LCOE surface off-thread → emits the surface dict."""

    finished = Signal(object)
    error = Signal(str)

    def __init__(self, site, base, var_x, var_y, x_vals, y_vals):
        super().__init__()
        self._site = site
        self._base = base
        self._var_x = var_x
        self._var_y = var_y
        self._x_vals = x_vals
        self._y_vals = y_vals

    def run(self):
        try:
            surf = opt.lcoe_surface(
                self._site, self._base, self._var_x, self._var_y,
                self._x_vals, self._y_vals,
            )
            self.finished.emit(surf)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class EconomicsWorker(QThread):
    """Run the on-design + degradation + NPV-LCOE chain off-thread."""

    finished = Signal(object)
    error = Signal(str)

    def __init__(self, config, t_ww, t_cw, dist_shore, deg_model, deg_rate, deg_kw):
        super().__init__()
        self._config = config
        self._t_ww = t_ww
        self._t_cw = t_cw
        self._dist = dist_shore
        self._deg_model = deg_model
        self._deg_rate = deg_rate
        self._deg_kw = deg_kw or {}

    def run(self):
        try:
            out = eco.analyze(
                self._config, self._t_ww, self._t_cw, self._dist,
                deg_model=self._deg_model, deg_rate=self._deg_rate,
                **self._deg_kw,
            )
            self.finished.emit(out)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class OperationWorker(QThread):
    """Run time-series operation + diagnosis off-thread."""

    finished = Signal(object)
    error = Signal(str)

    def __init__(self, config, t_ww_design, t_cw_design, dist_shore,
                 ww_profile, cw_profile):
        super().__init__()
        self._config = config
        self._t_ww_d = t_ww_design
        self._t_cw_d = t_cw_design
        self._dist = dist_shore
        self._ww = ww_profile
        self._cw = cw_profile

    def run(self):
        try:
            out = oper.run_operation(
                self._config, self._t_ww_d, self._t_cw_d, self._dist,
                self._ww, self._cw,
            )
            out["diagnosis"] = oper.diagnose(out["result"], out["plant"])
            out["ww"] = self._ww
            out["cw"] = self._cw
            self.finished.emit(out)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class UQWorker(QThread):
    """Run a Monte Carlo / Tornado / Sobol study off-thread."""

    finished = Signal(object)
    error = Signal(str)

    def __init__(self, kind, config, t_ww, t_cw, params, opts):
        super().__init__()
        self._kind = kind
        self._config = config
        self._t_ww = t_ww
        self._t_cw = t_cw
        self._params = params
        self._opts = opts or {}

    def run(self):
        try:
            if self._kind == "mc":
                out = _uq.run_monte_carlo(
                    self._config, self._t_ww, self._t_cw, self._params, **self._opts)
            elif self._kind == "tornado":
                out = _uq.run_tornado(
                    self._config, self._t_ww, self._t_cw, self._params, **self._opts)
            elif self._kind == "sobol":
                out = _uq.run_sobol(
                    self._config, self._t_ww, self._t_cw, self._params, **self._opts)
            else:
                raise ValueError(f"unknown UQ kind: {self._kind}")
            out["kind"] = self._kind
            self.finished.emit(out)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class ClimateDeltaWorker(QThread):
    """Fetch an SSP climate delta off-thread (NETWORK; may fail gracefully)."""

    finished = Signal(object)
    error = Signal(str)

    def __init__(self, scenario, year, lon, lat, depth):
        super().__init__()
        self._args = (scenario, year, lon, lat, depth)

    def run(self):
        try:
            out = _rsrc.fetch_climate_delta(*self._args)
            self.finished.emit(out)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class HazardEnrichWorker(QThread):
    """Enrich sites with siting hazard layers off-thread (NETWORK)."""

    finished = Signal(object)
    error = Signal(str)

    def __init__(self, sites):
        super().__init__()
        self._sites = sites

    def run(self):
        try:
            df = _rsrc.enrich_hazards(self._sites)
            self.finished.emit(df)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class RegionalWorker(QThread):
    """Batch-optimize every site in a region off-thread (NETWORK, slow)."""

    finished = Signal(object)
    error = Signal(str)

    def __init__(self, region, kwargs):
        super().__init__()
        self._region = region
        self._kwargs = kwargs or {}

    def run(self):
        try:
            df = _reg.run_regional(self._region, **self._kwargs)
            self.finished.emit(df)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
