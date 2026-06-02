"""Bokeh-based interactive charts for results visualization.

Mirrors the 13 chart types from results_charts.py using Bokeh for
interactive charts rendered in QWebEngineView.  Uses json_item() for
clean JSON serialisation (no binary encoding issues).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import h5py
import numpy as np

from bokeh.embed import json_item
from bokeh.layouts import column, gridplot, row
from bokeh.models import (
    BasicTicker,
    ColorBar,
    ColumnDataSource,
    HoverTool,
    Label,
    Legend,
    LegendItem,
    LinearAxis,
    LinearColorMapper,
    NumeralTickFormatter,
    Range1d,
    Span,
    Title,
)
from bokeh.palettes import (
    Category10_10,
    RdBu11,
    Turbo256,
)
from bokeh.plotting import figure

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QWidget,
)

from esfex.utils.temporal import HOURS_STD_YEAR
from esfex.visualization.i18n import tr
from esfex.visualization.panels.results_charts import (
    _sorted_scenarios,
    _load_gen_data,
    _load_bat_data,
    _get_temporal_res,
    _prefixed,
    _get_gen_types,
    _get_node_names,
    _canonical_tech_name,
    _categorize_gen_names,
    _aggregate_by_technology,
    _load_gen_configs,
    _load_bat_configs,
    _load_tech_configs,
    _load_bat_tech_configs,
    _load_investment_data,
    _load_decommissioning_data,
    _parse_invest_cost,
    _sum_nodes,
    _aggregate,
    _year_hours,
    _color_for,
    _is_renewable,
)
from esfex.visualization.theme import current_theme, get_tab10

logger = logging.getLogger(__name__)

MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

_FUEL_COLORS = [
    "#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6",
    "#1abc9c", "#e67e22", "#34495e", "#d35400", "#7f8c8d",
]


# ──────────────────────────────────────────────────────────────
# Theme helpers
# ──────────────────────────────────────────────────────────────

def _themed_figure(**kwargs) -> figure:
    """Create a Bokeh figure with the active ESFEX theme."""
    theme = current_theme()
    c = theme.colors
    defaults = dict(
        sizing_mode="stretch_width",
        tools="pan,wheel_zoom,box_zoom,reset,save",
        active_scroll="wheel_zoom",
        background_fill_color=c.surface_primary,
        border_fill_color=c.surface_primary,
    )
    defaults.update(kwargs)
    p = figure(**defaults)
    p.title.text_color = c.text_primary
    p.xaxis.axis_label_text_color = c.text_primary
    p.yaxis.axis_label_text_color = c.text_primary
    p.xaxis.major_label_text_color = c.text_primary
    p.yaxis.major_label_text_color = c.text_primary
    p.xaxis.axis_line_color = c.text_secondary
    p.yaxis.axis_line_color = c.text_secondary
    p.xgrid.grid_line_color = c.border_light
    p.ygrid.grid_line_color = c.border_light
    return p


def _style_legend(p, location="top_left", font_size="8pt", alpha=0.7):
    """Apply legend styling only if the figure has legends."""
    if not p.legend:
        return
    p.legend.location = location
    p.legend.click_policy = "hide"
    p.legend.label_text_font_size = font_size
    p.legend.background_fill_alpha = alpha


def _empty_figure(msg: str = "No data") -> figure:
    """Return a themed figure with a centered message."""
    p = _themed_figure(title=msg, height=400,
                       x_range=Range1d(0, 1), y_range=Range1d(0, 1))
    p.scatter([0.5], [0.5], size=0, alpha=0)  # invisible renderer
    p.add_layout(Label(
        x=200, y=200, text=msg,
        text_font_size="14pt", text_color="#888",
        x_units="screen", y_units="screen",
    ))
    p.xaxis.visible = False
    p.yaxis.visible = False
    p.xgrid.visible = False
    p.ygrid.visible = False
    return p


# ──────────────────────────────────────────────────────────────
# Base class
# ──────────────────────────────────────────────────────────────

class BokehChart:
    """Base class for Bokeh-based charts."""

    TITLE = "Chart"
    TR_KEY = ""

    def build_figure(self, h5_path: Path, years: list[int], **kwargs) -> Any:
        """Build and return a Bokeh model.  Subclasses must override."""
        raise NotImplementedError

    def get_params_widget(self) -> Optional[QWidget]:
        """Override to return a Qt controls widget for chart parameters."""
        return None

    def safe_build(self, h5_path: Path, years: list[int], **kwargs) -> Any:
        """Build figure with error handling."""
        try:
            return self.build_figure(h5_path, years, **kwargs)
        except Exception as e:
            logger.exception("Chart %s failed: %s", self.TITLE, e)
            return _empty_figure(f"Error: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 1 — Generation Mix
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class GenerationMixChart(BokehChart):
    TITLE = "Generation Mix"
    TR_KEY = "results_charts.gen_mix"

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> Any:
        with h5py.File(h5_path, "r") as h5f:
            bp = kw.get("base_prefix", "")
            tres = _get_temporal_res(h5f)
            gen_configs = _load_gen_configs(h5f, bp)
            tech_configs = _load_tech_configs(h5f, bp)
            bat_tech_configs = _load_bat_tech_configs(h5f, bp)

            all_gen: dict[str, list] = {}
            demand_all: list = []
            year_list: list[int] = []
            total_months = 0
            gen_inv_by_tech: dict[str, np.ndarray] = {}
            bat_inv_by_tech: dict[str, np.ndarray] = {}
            ret_by_tech: dict[str, np.ndarray] = {}
            total_cost_by_year: list[float] = []
            master_re_targets: list[float] = []
            scenarios = list(_sorted_scenarios(h5f, bp))
            prev_retirement_fracs: dict[int, float] = {}

            for sc_key, year in scenarios:
                sc = h5f[_prefixed(bp, "detailed_results")][sc_key]
                master_re_targets.append(
                    float(sc.attrs.get("master_re_target", 0.0)) * 100
                )
                year_list.append(year)
                months_this_year = 0

                gen_data = _load_gen_data(sc)
                tech_monthly = _aggregate_by_technology(gen_data, tres, "monthly")
                for tech_label, monthly in tech_monthly.items():
                    all_gen.setdefault(tech_label, []).extend(monthly.tolist())
                    months_this_year = max(months_this_year, len(monthly))

                for bkey, label in [("battery_discharge", "Battery discharge"),
                                    ("battery_charge", "Battery charge")]:
                    bat = _load_bat_data(sc, bkey)
                    if bat:
                        year_total = None
                        for _, arr in bat.items():
                            t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                            m = np.array(_aggregate(t, "monthly", tres))
                            if year_total is None:
                                year_total = m
                            else:
                                ml = min(len(year_total), len(m))
                                year_total[:ml] += m[:ml]
                            months_this_year = max(months_this_year, len(m))
                        if year_total is not None:
                            all_gen.setdefault(label, []).extend(year_total.tolist())

                if "curtailment" in sc:
                    curt = sc["curtailment"][:]
                    ct = _sum_nodes(curt) if curt.ndim >= 2 else curt
                    cm = _aggregate(ct, "monthly", tres)
                    all_gen.setdefault("Curtailment", []).extend(cm.tolist())

                if "demand" in sc:
                    dem = sc["demand"][:]
                    dt = _sum_nodes(dem) if dem.ndim >= 2 else dem
                    dm = _aggregate(dt, "monthly", tres)
                    demand_all.extend(dm.tolist())
                    months_this_year = max(months_this_year, len(dm))

                if months_this_year == 0:
                    months_this_year = 12
                total_months += months_this_year

                # Investments
                year_idx = len(year_list) - 1
                inv_data = _load_investment_data(
                    sc, tech_configs=tech_configs,
                    bat_tech_configs=bat_tech_configs,
                )
                year_cost = 0.0
                for tech_name, inv_mw in inv_data.get("tech_investments", {}).items():
                    if inv_mw > 0:
                        canon, _ = _canonical_tech_name(tech_name)
                        if canon not in gen_inv_by_tech:
                            gen_inv_by_tech[canon] = np.zeros(len(scenarios))
                        gen_inv_by_tech[canon][year_idx] += float(inv_mw) / 1000
                        cost_per_mw = inv_data.get("tech_costs", {}).get(tech_name, 0)
                        year_cost += float(inv_mw) * cost_per_mw / 1e6
                for bt_name, inv_mw in inv_data.get("bat_tech_power_investments", {}).items():
                    if inv_mw > 0:
                        canon, _ = _canonical_tech_name(bt_name)
                        if canon not in bat_inv_by_tech:
                            bat_inv_by_tech[canon] = np.zeros(len(scenarios))
                        bat_inv_by_tech[canon][year_idx] += float(inv_mw) / 1000
                        cost_per_mw = inv_data.get("bat_tech_costs", {}).get(bt_name, 0)
                        year_cost += float(inv_mw) * cost_per_mw / 1e6

                if "gen_investment_power" in inv_data:
                    for gi, inv_mw in enumerate(inv_data["gen_investment_power"]):
                        if inv_mw > 0 and gi < len(gen_configs):
                            gc = gen_configs[gi]
                            gn = gc.get("name", f"Gen_{gi}")
                            if isinstance(gn, bytes):
                                gn = gn.decode()
                            gn_canon, _ = _canonical_tech_name(gn)
                            if gn_canon not in gen_inv_by_tech:
                                gen_inv_by_tech[gn_canon] = np.zeros(len(scenarios))
                            gen_inv_by_tech[gn_canon][year_idx] += float(inv_mw) / 1000
                            if "invest_cost" in gc:
                                year_cost += float(inv_mw) * _parse_invest_cost(gc["invest_cost"]) / 1e6
                if "bat_investment_power" in inv_data:
                    for bi, inv_mw in enumerate(inv_data["bat_investment_power"]):
                        if inv_mw > 0:
                            bat_configs = _load_bat_configs(h5f, bp)
                            if bi < len(bat_configs):
                                bc = bat_configs[bi]
                                bn = bc.get("name", f"Battery_{bi}")
                                if isinstance(bn, bytes):
                                    bn = bn.decode()
                                bn_canon, _ = _canonical_tech_name(bn)
                                if bn_canon not in bat_inv_by_tech:
                                    bat_inv_by_tech[bn_canon] = np.zeros(len(scenarios))
                                bat_inv_by_tech[bn_canon][year_idx] += float(inv_mw) / 1000
                total_cost_by_year.append(year_cost)

                decomm, raw_fracs = _load_decommissioning_data(sc, gen_configs=gen_configs)
                for g_idx, frac in raw_fracs.items():
                    prev = prev_retirement_fracs.get(g_idx, 0.0)
                    delta = frac - prev
                    if delta > 1e-6 and g_idx < len(gen_configs):
                        gc = gen_configs[g_idx]
                        name = gc.get("name", f"Gen_{g_idx}")
                        if isinstance(name, bytes):
                            name = name.decode()
                        rated = gc.get("rated_power", 0)
                        rated_mw = _parse_invest_cost(rated)
                        inc_mw = delta * rated_mw
                        canon, _ = _canonical_tech_name(name)
                        if canon not in ret_by_tech:
                            ret_by_tech[canon] = np.zeros(len(scenarios))
                        ret_by_tech[canon][year_idx] += inc_mw / 1000
                prev_retirement_fracs = raw_fracs.copy()

        if not all_gen:
            return _empty_figure("No generation data")

        # Pad shorter series
        for key in all_gen:
            arr = np.array(all_gen[key])
            if len(arr) < total_months:
                arr = np.pad(arr, (0, total_months - len(arr)))
            all_gen[key] = arr

        demand_array = np.array(demand_all) if demand_all else None
        if demand_array is not None and len(demand_array) < total_months:
            demand_array = np.pad(demand_array, (0, total_months - len(demand_array)))

        cats = _categorize_gen_names(list(all_gen.keys()))

        # Aggregate categories
        _MAX = 6
        merged_gen: dict[str, np.ndarray] = {}
        merged_order: list[tuple[str, str]] = []
        for cat_key in ("renewable", "rooftop", "thermal", "storage_discharge",
                         "storage_charge", "curtailment", "spillage", "reserve"):
            members = [t for t in cats[cat_key] if t in all_gen and np.any(all_gen[t] > 0)]
            if len(members) <= _MAX:
                for t in members:
                    merged_gen[t] = all_gen[t]
                    merged_order.append((cat_key, t))
            else:
                agg_label = {"thermal": "Thermal", "renewable": "Renewable",
                             "storage_discharge": "Battery discharge",
                             "storage_charge": "Battery charge"}.get(cat_key, cat_key.title())
                merged_gen[agg_label] = sum(all_gen[t] for t in members)
                merged_order.append((cat_key, agg_label))

        x = list(range(total_months))
        # X tick labels at year boundaries
        x_tick_map = {}
        idx = 0
        for yr in year_list:
            x_tick_map[idx] = str(yr)
            idx += 12

        # ── Subplot a: Stacked areas ──
        p1 = _themed_figure(title="a) Generation Mix Evolution", height=420)
        p1.xaxis.major_label_overrides = x_tick_map
        p1.xaxis.axis_label = "Year"
        p1.yaxis.axis_label = "Energy (GWh)"

        pos_cats = {"renewable", "rooftop", "thermal", "storage_discharge"}
        cum_pos = np.zeros(total_months)
        for cat_key, label in merged_order:
            if cat_key in pos_cats:
                vals = merged_gen[label] / 1000
                new_cum = cum_pos + vals
                p1.varea(x=x, y1=cum_pos.tolist(), y2=new_cum.tolist(),
                         fill_color=_color_for(label), fill_alpha=0.8,
                         legend_label=label)
                cum_pos = new_cum

        neg_cats = {"storage_charge", "curtailment", "spillage", "reserve"}
        cum_neg = np.zeros(total_months)
        for cat_key, label in merged_order:
            if cat_key in neg_cats:
                vals = merged_gen[label] / 1000
                new_cum = cum_neg - vals
                p1.varea(x=x, y1=cum_neg.tolist(), y2=new_cum.tolist(),
                         fill_color=_color_for(label), fill_alpha=0.8,
                         legend_label=label)
                cum_neg = new_cum

        if demand_array is not None:
            p1.line(x, (demand_array / 1000).tolist(), line_color="black",
                    line_width=2.5, line_dash="dashed", legend_label="Total Demand")

        # Secondary y-axis for RE %
        renewable_total = sum(all_gen.get(t, np.zeros(1)) for t in cats["renewable"])
        thermal_total = sum(all_gen.get(t, np.zeros(1)) for t in cats["thermal"])
        total_gen_arr = renewable_total + thermal_total
        re_pen = np.divide(renewable_total, total_gen_arr,
                           out=np.zeros(total_months), where=total_gen_arr != 0) * 100
        p1.extra_y_ranges = {"re": Range1d(start=0, end=105)}
        p1.add_layout(LinearAxis(y_range_name="re", axis_label="RE Penetration (%)"), "right")
        p1.line(x, re_pen.tolist(), line_color="red", line_width=2.5,
                line_dash="dashed", y_range_name="re", legend_label="RE %")

        _style_legend(p1)

        # ── Subplot b: Investments & Retirements ──
        p2 = _themed_figure(title="b) Annual Capacity Investments & Retirements", height=280)
        if year_list:
            n_years = len(year_list)
            years_str = [str(y) for y in year_list]
            p2.x_range = p2.x_range  # reset
            p2.xaxis.axis_label = "Year"
            p2.yaxis.axis_label = "Capacity (GW)"

            # Aggregate investments
            inv_agg: dict[str, np.ndarray] = {}
            for tech in cats["renewable"]:
                if tech in gen_inv_by_tech and np.any(gen_inv_by_tech[tech] > 0):
                    inv_agg.setdefault(tech, np.zeros(n_years))
                    inv_agg[tech] += gen_inv_by_tech[tech][:n_years]
            for bn, inv_vals in bat_inv_by_tech.items():
                if np.any(inv_vals > 0):
                    label = f"{bn} (bat)"
                    inv_agg.setdefault(label, np.zeros(n_years))
                    inv_agg[label] += inv_vals[:n_years]
            thermal_inv = np.zeros(n_years)
            for tech in cats["thermal"]:
                if tech in gen_inv_by_tech and np.any(gen_inv_by_tech[tech] > 0):
                    thermal_inv += gen_inv_by_tech[tech][:n_years]
            if np.any(thermal_inv > 0):
                inv_agg["Thermal"] = thermal_inv

            # Stacked investment bars
            bottom_pos = np.zeros(n_years)
            for label, vals in inv_agg.items():
                p2.vbar(x=year_list, top=(bottom_pos + vals).tolist(),
                        bottom=bottom_pos.tolist(), width=0.7,
                        fill_color=_color_for(label), fill_alpha=0.85,
                        legend_label=f"{label} (inv)")
                bottom_pos = bottom_pos + vals

            # Retirements (negative)
            ret_agg: dict[str, np.ndarray] = {}
            for tech_name, ret_vals in ret_by_tech.items():
                if np.any(ret_vals > 0):
                    canon, _ = _canonical_tech_name(tech_name)
                    is_re = _is_renewable(canon) or _is_renewable(tech_name)
                    lbl = canon if is_re else "Thermal"
                    ret_agg.setdefault(lbl, np.zeros(n_years))
                    ret_agg[lbl] += ret_vals[:n_years]

            bottom_neg = np.zeros(n_years)
            for label, vals in ret_agg.items():
                p2.vbar(x=year_list, top=bottom_neg.tolist(),
                        bottom=(bottom_neg - vals).tolist(), width=0.7,
                        fill_color=_color_for(label), fill_alpha=0.4,
                        hatch_pattern="/",
                        legend_label=f"{label} (retired)")
                bottom_neg = bottom_neg - vals

            # Cost on secondary y
            cost_arr = np.array(total_cost_by_year[:n_years])
            if np.any(cost_arr > 0):
                p2.extra_y_ranges = {"cost": Range1d(start=0, end=float(cost_arr.max()) * 1.2)}
                p2.add_layout(LinearAxis(y_range_name="cost", axis_label="Cost (M$)"), "right")
                p2.line(year_list, cost_arr.tolist(), line_color="green",
                        line_width=2.5, y_range_name="cost", legend_label="Inv Cost (M$)")
                p2.scatter(year_list, cost_arr.tolist(), fill_color="green",
                           size=5, y_range_name="cost")

            _style_legend(p2, font_size="7pt")

        return column(p1, p2, sizing_mode="stretch_both")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 2 — Demand Coverage Detail
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DemandCoverageChart(BokehChart):
    TITLE = "Demand Coverage"
    TR_KEY = "results_charts.demand_coverage"

    def __init__(self):
        self._start_day = 0
        self._num_days = 7
        self._year_idx = 0

    def get_params_widget(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel(tr("results_charts.start_day")))
        self._day_spin = QSpinBox()
        self._day_spin.setRange(0, 364)
        self._day_spin.setValue(0)
        self._day_spin.valueChanged.connect(lambda v: setattr(self, "_start_day", v))
        lay.addWidget(self._day_spin)
        lay.addWidget(QLabel(tr("results_charts.num_days")))
        self._ndays_spin = QSpinBox()
        self._ndays_spin.setRange(1, 365)
        self._ndays_spin.setValue(7)
        self._ndays_spin.valueChanged.connect(lambda v: setattr(self, "_num_days", v))
        lay.addWidget(self._ndays_spin)
        lay.addStretch()
        return w

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> Any:
        year_idx = self._year_idx
        year = 0

        with h5py.File(h5_path, "r") as h5f:
            bp = kw.get("base_prefix", "")
            tres = _get_temporal_res(h5f)
            scenarios = list(_sorted_scenarios(h5f, bp))
            if year_idx >= len(scenarios):
                year_idx = len(scenarios) - 1
            if year_idx < 0:
                return _empty_figure("No scenarios")

            sc_key, year = scenarios[year_idx]
            sc = h5f[_prefixed(bp, "detailed_results")][sc_key]
            spd = max(1, 24 // tres)
            start_h = self._start_day * spd
            end_h = min((self._start_day + self._num_days) * spd, _year_hours(tres))
            num_hours = end_h - start_h
            time_hours = list(range(num_hours))

            gen_data = _load_gen_data(sc)
            generation: dict[str, np.ndarray] = {}
            for name, arr in gen_data.items():
                total = _sum_nodes(arr) if arr.ndim >= 2 else arr
                generation[name] = total[start_h:end_h]

            cats = _categorize_gen_names(list(generation.keys()))
            _MAX = 8
            merged: dict[str, np.ndarray] = {}
            merged_order: list[tuple[str, str]] = []
            for cat_key in ("renewable", "rooftop", "thermal", "storage_discharge"):
                members = [t for t in cats[cat_key] if t in generation and np.any(generation[t] > 0)]
                if len(members) <= _MAX:
                    for t in members:
                        merged[t] = generation[t]
                        merged_order.append((cat_key, t))
                else:
                    label = {"thermal": "Thermal", "renewable": "Renewable"}.get(
                        cat_key, cat_key.replace("_", " ").title())
                    total = np.zeros(num_hours)
                    for t in members:
                        d = generation[t]
                        ml = min(len(total), len(d))
                        total[:ml] += d[:ml]
                    merged[label] = total
                    merged_order.append((cat_key, label))

        end_day = min(self._start_day + self._num_days, 365)
        p = _themed_figure(
            title=f"Demand Coverage — Year {year} (Days {self._start_day + 1}-{end_day})",
            height=500,
        )
        p.xaxis.axis_label = "Hour"
        p.yaxis.axis_label = "Power (MW)"

        cum = np.zeros(num_hours)
        for cat_key, tech in merged_order:
            d = merged[tech].copy()
            if len(d) < num_hours:
                d = np.pad(d, (0, num_hours - len(d)))
            new_cum = cum + d
            p.varea(x=time_hours, y1=cum.tolist(), y2=new_cum.tolist(),
                    fill_color=_color_for(tech), fill_alpha=0.8,
                    legend_label=tech)
            cum = new_cum

        # Battery charge (negative)
        with h5py.File(h5_path, "r") as h5f:
            bp = kw.get("base_prefix", "")
            tres = _get_temporal_res(h5f)
            scenarios = list(_sorted_scenarios(h5f, bp))
            if year_idx < len(scenarios):
                sc_key, _ = scenarios[year_idx]
                sc = h5f[_prefixed(bp, "detailed_results")][sc_key]
                spd = max(1, 24 // tres)
                start_h = self._start_day * spd
                end_h = min((self._start_day + self._num_days) * spd, _year_hours(tres))
                bat_charge = _load_bat_data(sc, "battery_charge")
                bat_ch_total = np.zeros(num_hours)
                for _, arr in bat_charge.items():
                    t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                    slc = t[start_h:end_h]
                    ml = min(len(bat_ch_total), len(slc))
                    bat_ch_total[:ml] += slc[:ml]
                if np.any(bat_ch_total > 0):
                    p.varea(x=time_hours, y1=[0] * num_hours,
                            y2=(-bat_ch_total).tolist(),
                            fill_color=_color_for("Battery charge"), fill_alpha=0.8,
                            legend_label="Battery charge")

                # Demand line
                if "demand" in sc:
                    dem = sc["demand"][:]
                    dt = _sum_nodes(dem) if dem.ndim >= 2 else dem
                    demand_data = dt[start_h:end_h]
                    p.line(time_hours[:len(demand_data)], demand_data.tolist(),
                           line_color="black", line_width=2.5, legend_label="Demand")

        _style_legend(p)
        return p


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 3 — Battery Heatmap
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BatteryHeatmapChart(BokehChart):
    TITLE = "Battery Heatmap"
    TR_KEY = "results_charts.battery_heatmap"

    def __init__(self):
        self._sigma = 1.0

    def get_params_widget(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel(tr("results_charts.smoothing")))
        sp = QDoubleSpinBox()
        sp.setRange(0.0, 5.0)
        sp.setSingleStep(0.1)
        sp.setValue(1.0)
        sp.valueChanged.connect(lambda v: setattr(self, "_sigma", v))
        lay.addWidget(sp)
        lay.addStretch()
        return w

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> Any:
        rows_data = []
        y_labels = []

        with h5py.File(h5_path, "r") as h5f:
            bp = kw.get("base_prefix", "")
            tres = _get_temporal_res(h5f)
            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = h5f[_prefixed(bp, "detailed_results")][sc_key]
                charge_d = _load_bat_data(sc, "battery_charge")
                discharge_d = _load_bat_data(sc, "battery_discharge")
                total_c = np.zeros(1)
                total_d = np.zeros(1)
                for arr in charge_d.values():
                    t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                    if len(t) > len(total_c):
                        total_c = np.zeros(len(t))
                    total_c[:len(t)] += t
                for arr in discharge_d.values():
                    t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                    if len(t) > len(total_d):
                        total_d = np.zeros(len(t))
                    total_d[:len(t)] += t
                net = total_c - total_d
                monthly = _aggregate(net, "monthly", tres)
                if len(monthly) > 0:
                    padded = np.zeros(12)
                    padded[:min(len(monthly), 12)] = monthly[:12]
                    rows_data.append(padded)
                    y_labels.append(str(year))

        if not rows_data:
            return _empty_figure("No battery data")

        data = np.array(rows_data).T  # [12 months × years]
        if self._sigma > 0:
            from scipy.ndimage import gaussian_filter
            data = gaussian_filter(data, sigma=self._sigma)

        return _heatmap_figure(
            data, x_labels=y_labels, y_labels=MONTHS,
            title="Monthly Net Battery Flow (Charge - Discharge)",
            colorbar_title="MWh", palette="Turbo256",
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 4 — Battery Operation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BatteryOperationChart(BokehChart):
    TITLE = "Battery Operation"
    TR_KEY = "results_charts.battery_operation"

    def __init__(self):
        self._resolution = "daily"

    def get_params_widget(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel(tr("results_charts.resolution")))
        cb = QComboBox()
        cb.addItems(["daily", "monthly", "yearly"])
        cb.setCurrentText("daily")
        cb.currentTextChanged.connect(lambda v: setattr(self, "_resolution", v))
        lay.addWidget(cb)
        lay.addStretch()
        return w

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> Any:
        year_idx = kw.get("year_idx", 0)
        year = 0
        charge_d, discharge_d = {}, {}

        with h5py.File(h5_path, "r") as h5f:
            bp = kw.get("base_prefix", "")
            tres = _get_temporal_res(h5f)
            scenarios = list(_sorted_scenarios(h5f, bp))
            if year_idx < len(scenarios):
                sc_key, year = scenarios[year_idx]
                sc = h5f[_prefixed(bp, "detailed_results")][sc_key]
                charge_d = _load_bat_data(sc, "battery_charge")
                discharge_d = _load_bat_data(sc, "battery_discharge")

        charge_total = np.zeros(1)
        for arr in charge_d.values():
            t = _sum_nodes(arr) if arr.ndim >= 2 else arr
            if len(t) > len(charge_total):
                charge_total = np.zeros(len(t))
            charge_total[:len(t)] += t
        discharge_total = np.zeros(1)
        for arr in discharge_d.values():
            t = _sum_nodes(arr) if arr.ndim >= 2 else arr
            if len(t) > len(discharge_total):
                discharge_total = np.zeros(len(t))
            discharge_total[:len(t)] += t

        c_agg = np.array(_aggregate(charge_total, self._resolution, tres)) / 1e3
        d_agg = np.array(_aggregate(discharge_total, self._resolution, tres)) / 1e3

        p = _themed_figure(
            title=f"Battery Operation — Year {year} ({self._resolution})",
            height=500,
        )
        p.xaxis.axis_label = self._resolution.capitalize()
        p.yaxis.axis_label = "Energy (GWh)"

        x = list(range(len(c_agg)))
        p.vbar(x=x, top=c_agg.tolist(), width=0.8,
               fill_color="#9B59B6", fill_alpha=0.85, legend_label="Charge")
        p.vbar(x=x, top=(-d_agg).tolist(), width=0.8,
               fill_color="#3498DB", fill_alpha=0.85, legend_label="Discharge")

        _style_legend(p)
        return p


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 5 — Generation by Source
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class GenerationBySourceChart(BokehChart):
    TITLE = "Generation by Source"
    TR_KEY = "results_charts.gen_by_source"

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> Any:
        gen_annual: dict[str, list[float]] = {}
        year_list = []

        with h5py.File(h5_path, "r") as h5f:
            bp = kw.get("base_prefix", "")
            tres = _get_temporal_res(h5f)
            gen_types = _get_gen_types(h5f, bp)
            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = h5f[_prefixed(bp, "detailed_results")][sc_key]
                year_list.append(year)
                gen_data = _load_gen_data(sc)
                yh = _year_hours(tres)
                for name, arr in gen_data.items():
                    total = _sum_nodes(arr) if arr.ndim >= 2 else arr
                    gwh = total[:yh].sum() * tres / 1e3
                    gen_annual.setdefault(name, []).append(gwh)

        if not gen_annual:
            return _empty_figure("No data")

        re_agg: dict[str, np.ndarray] = {}
        th_agg: dict[str, np.ndarray] = {}
        n_years = len(year_list)
        for name, vals in gen_annual.items():
            gtype = gen_types.get(name, "")
            is_re = _is_renewable(name) or gtype == "Renewable"
            canon, _ = _canonical_tech_name(name)
            target = re_agg if is_re else th_agg
            arr = np.array(vals[:n_years])
            if canon in target:
                ml = min(len(target[canon]), len(arr))
                target[canon][:ml] += arr[:ml]
            else:
                target[canon] = arr.copy()

        _MAX_LINES = 10
        if len(th_agg) > _MAX_LINES:
            sorted_th = sorted(th_agg.items(), key=lambda kv: kv[1].sum(), reverse=True)
            top = dict(sorted_th[:_MAX_LINES - 1])
            other = sum((v for _, v in sorted_th[_MAX_LINES - 1:]), np.zeros(n_years))
            if np.any(other > 0):
                top["Other thermal"] = other
            th_agg = top

        p1 = _themed_figure(title="Renewable Sources", height=350)
        p1.yaxis.axis_label = "Annual Generation (GWh)"
        for name, vals in re_agg.items():
            p1.line(year_list[:len(vals)], vals.tolist(), line_color=_color_for(name),
                    line_width=2, legend_label=name)
            p1.scatter(year_list[:len(vals)], vals.tolist(), fill_color=_color_for(name), size=5)

        p2 = _themed_figure(title="Thermal / Conventional", height=350)
        p2.xaxis.axis_label = "Year"
        p2.yaxis.axis_label = "Annual Generation (GWh)"
        for name, vals in th_agg.items():
            p2.line(year_list[:len(vals)], vals.tolist(), line_color=_color_for(name),
                    line_width=2, legend_label=name)
            p2.scatter(year_list[:len(vals)], vals.tolist(), fill_color=_color_for(name), size=5)

        for p in (p1, p2):
            _style_legend(p)

        return column(p1, p2, sizing_mode="stretch_both")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 6 — Scenarios Comparison
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ScenariosComparisonChart(BokehChart):
    TITLE = "Scenarios Comparison"
    TR_KEY = "results_charts.scenarios_comparison"

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> Any:
        yr_list, re_pct_list, total_gwh_list = [], [], []
        with h5py.File(h5_path, "r") as h5f:
            bp = kw.get("base_prefix", "")
            tres = _get_temporal_res(h5f)
            gen_types = _get_gen_types(h5f, bp)
            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = h5f[_prefixed(bp, "detailed_results")][sc_key]
                gen_data = _load_gen_data(sc)
                yh = _year_hours(tres)
                total, re_total = 0.0, 0.0
                for name, arr in gen_data.items():
                    t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                    s = t[:yh].sum() * tres
                    total += s
                    gtype = gen_types.get(name, "")
                    if _is_renewable(name) or gtype == "Renewable":
                        re_total += s
                yr_list.append(year)
                re_pct_list.append(re_total / total * 100 if total > 0 else 0)
                total_gwh_list.append(total / 1e3)

        if not yr_list:
            return _empty_figure("No data")

        p1 = _themed_figure(title="RE Penetration Evolution", height=400)
        p1.yaxis.axis_label = "RE Penetration (%)"
        p1.y_range = Range1d(0, 105)
        p1.varea(x=yr_list, y1=[0] * len(yr_list), y2=re_pct_list,
                 fill_color="rgba(39,174,96,0.2)")
        p1.line(yr_list, re_pct_list, line_color="#27ae60", line_width=2,
                legend_label="RE %")
        p1.scatter(yr_list, re_pct_list, fill_color="#27ae60", size=5)

        p2 = _themed_figure(title="Total Generation per Year", height=400)
        p2.yaxis.axis_label = "Total Generation (GWh)"
        p2.vbar(x=yr_list, top=total_gwh_list, width=0.7,
                fill_color="#3498db", fill_alpha=0.7, legend_label="Total Gen")

        for p in (p1, p2):
            _style_legend(p)

        return row(p1, p2, sizing_mode="stretch_both")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 7 — Net Load Heatmap
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class NetLoadHeatmapChart(BokehChart):
    TITLE = "Net Load Heatmap"
    TR_KEY = "results_charts.net_load_heatmap"

    def __init__(self):
        self._sigma = 1.0

    def get_params_widget(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel(tr("results_charts.smoothing")))
        sp = QDoubleSpinBox()
        sp.setRange(0.0, 5.0)
        sp.setSingleStep(0.1)
        sp.setValue(1.0)
        sp.valueChanged.connect(lambda v: setattr(self, "_sigma", v))
        lay.addWidget(sp)
        lay.addStretch()
        return w

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> Any:
        import pandas as pd

        net_load_all = []
        yr_list = []
        with h5py.File(h5_path, "r") as h5f:
            bp = kw.get("base_prefix", "")
            tres = _get_temporal_res(h5f)
            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = h5f[_prefixed(bp, "detailed_results")][sc_key]
                if "demand" not in sc:
                    continue
                dem = sc["demand"][:]
                demand = _sum_nodes(dem) if dem.ndim >= 2 else dem
                yh = _year_hours(tres)
                demand = demand[:yh]
                gen_data = _load_gen_data(sc)
                re_gen = np.zeros(yh)
                for name, arr in gen_data.items():
                    nl = name.lower()
                    if "wind" in nl or "solar" in nl:
                        t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                        t = t[:yh]
                        re_gen[:len(t)] += t
                net_load = demand - re_gen[:len(demand)]
                net_load_all.extend(net_load.tolist())
                yr_list.append(year)

        if not net_load_all:
            return _empty_figure("No data")

        freq = f"{tres}h"
        start_yr = min(yr_list) if yr_list else 2025
        idx = pd.date_range(start=f"{start_yr}-01-01", periods=len(net_load_all), freq=freq)
        df = pd.DataFrame({"NL": net_load_all}, index=idx)
        df["ramp"] = df["NL"].diff()

        avg_nl = df.groupby([df.index.month, df.index.hour])["NL"].mean().unstack()
        avg_ramp = df.groupby([df.index.month, df.index.hour])["ramp"].mean().unstack()

        figs = []
        for data, title, palette in [
            (avg_nl, "Avg Net Load (MW)", "Turbo256"),
            (avg_ramp, "Avg Net Load Ramp (MW/h)", "RdBu11"),
        ]:
            vals = data.fillna(0).values
            if self._sigma > 0:
                from scipy.ndimage import gaussian_filter
                vals = gaussian_filter(vals, sigma=self._sigma)
            hour_labels = [str(int(h)) for h in data.columns]
            month_labels = [MONTHS[m - 1] if 1 <= m <= 12 else str(m) for m in data.index]
            figs.append(_heatmap_figure(
                vals, x_labels=hour_labels, y_labels=month_labels,
                title=title, colorbar_title="MW",
                palette=palette, x_axis_label="Hour of Day",
            ))

        return row(*figs, sizing_mode="stretch_both")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 8 — CF / LCOE / VALLCOE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CFLcoeVallcoeChart(BokehChart):
    TITLE = "CF / LCOE / VALLCOE"
    TR_KEY = "results_charts.cf_lcoe_vallcoe"

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> Any:
        records = []
        with h5py.File(h5_path, "r") as h5f:
            bp = kw.get("base_prefix", "")
            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = h5f[_prefixed(bp, "detailed_results")][sc_key]
                for prefix, is_bat in [("capacity_factor", False),
                                       ("battery_capacity_factor", True)]:
                    if prefix not in sc:
                        continue
                    grp = sc[prefix]
                    lcoe_key = "battery_lcoe" if is_bat else "lcoe"
                    vallcoe_key = "battery_vallcoe" if is_bat else "vallcoe"
                    for name in grp:
                        tech_label = f"Storage: {name.replace('_', ' ')}" if is_bat else _canonical_tech_name(name)[0]
                        cf_arr = grp[name][:]
                        if cf_arr.ndim == 2:
                            for node in range(cf_arr.shape[0]):
                                cf_vals = cf_arr[node, :]
                                avg_cf = float(np.mean(cf_vals[cf_vals > 0]) * 100) if np.any(cf_vals > 0) else 0
                                avg_lcoe, avg_vallcoe = 0.0, 0.0
                                if lcoe_key in sc and name in sc[lcoe_key]:
                                    la = sc[lcoe_key][name][:]
                                    if la.ndim == 2:
                                        lv = la[node, :]
                                        avg_lcoe = float(np.mean(lv[lv > 0])) if np.any(lv > 0) else 0
                                if vallcoe_key in sc and name in sc[vallcoe_key]:
                                    va = sc[vallcoe_key][name][:]
                                    if va.ndim == 2:
                                        vv = va[node, :]
                                        avg_vallcoe = float(np.mean(vv[vv > 0])) if np.any(vv > 0) else 0
                                if avg_cf > 0:
                                    records.append(dict(tech=tech_label, year=year,
                                                        cf=avg_cf, lcoe=avg_lcoe, vallcoe=avg_vallcoe))
                        else:
                            avg_cf = float(np.mean(cf_arr[cf_arr > 0]) * 100) if np.any(cf_arr > 0) else 0
                            avg_lcoe, avg_vallcoe = 0.0, 0.0
                            if lcoe_key in sc and name in sc[lcoe_key]:
                                la = sc[lcoe_key][name][:]
                                avg_lcoe = float(np.mean(la[la > 0])) if np.any(la > 0) else 0
                            if vallcoe_key in sc and name in sc[vallcoe_key]:
                                va = sc[vallcoe_key][name][:]
                                avg_vallcoe = float(np.mean(va[va > 0])) if np.any(va > 0) else 0
                            if avg_cf > 0:
                                records.append(dict(tech=tech_label, year=year,
                                                    cf=avg_cf, lcoe=avg_lcoe, vallcoe=avg_vallcoe))

        if not records:
            return _empty_figure("No CF/LCOE data")

        techs = sorted(set(r["tech"] for r in records))
        colors = get_tab10()

        # CF scatter plot (grouped by technology)
        p1 = _themed_figure(title="Capacity Factors (%)", height=400,
                            x_range=techs)
        p1.y_range = Range1d(0, 100)
        p1.yaxis.axis_label = "Capacity Factor (%)"
        for i, tech in enumerate(techs):
            cfs = [r["cf"] for r in records if r["tech"] == tech]
            c = colors[i % len(colors)]
            # Jitter x positions
            jitter = np.random.uniform(-0.3, 0.3, len(cfs))
            x_pos = [i + j for j in jitter]
            p1.scatter(x_pos, cfs, fill_color=c, fill_alpha=0.6, size=5,
                       legend_label=tech)
        p1.xaxis.major_label_orientation = 0.7

        # LCOE / VALLCOE scatter
        p2 = _themed_figure(title="LCOE / VALCOE ($/MWh)", height=400,
                            x_range=techs)
        p2.yaxis.axis_label = "$/MWh"
        for i, tech in enumerate(techs):
            c = colors[i % len(colors)]
            lcoe_pts = [r["lcoe"] for r in records if r["tech"] == tech and r["lcoe"] > 0]
            if lcoe_pts:
                jitter = np.random.uniform(-0.2, 0.2, len(lcoe_pts))
                kw = {"legend_label": "LCOE"} if i == 0 else {}
                p2.scatter([i + j for j in jitter], lcoe_pts, fill_color=c,
                           marker="triangle", size=7, **kw)
            vallcoe_pts = [r["vallcoe"] for r in records if r["tech"] == tech and r["vallcoe"] > 0]
            if vallcoe_pts:
                jitter = np.random.uniform(-0.2, 0.2, len(vallcoe_pts))
                kw = {"legend_label": "VALCOE"} if i == 0 else {}
                p2.scatter([i + j for j in jitter], vallcoe_pts, fill_color=c,
                           marker="square", size=7, **kw)
        p2.xaxis.major_label_orientation = 0.7

        for p in (p1, p2):
            _style_legend(p, location="top_right")

        return column(p1, p2, sizing_mode="stretch_both")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 9 — Electricity Cost
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ElectricityCostChart(BokehChart):
    TITLE = "Electricity Cost"
    TR_KEY = "results_charts.electricity_cost"

    def __init__(self):
        self._sigma = 1.0

    def get_params_widget(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel(tr("results_charts.smoothing")))
        sp = QDoubleSpinBox()
        sp.setRange(0.0, 5.0)
        sp.setSingleStep(0.1)
        sp.setValue(1.0)
        sp.valueChanged.connect(lambda v: setattr(self, "_sigma", v))
        lay.addWidget(sp)
        lay.addStretch()
        return w

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> Any:
        with h5py.File(h5_path, "r") as h5f:
            bp = kw.get("base_prefix", "")
            tres = _get_temporal_res(h5f)
            spd = max(1, 24 // tres)
            yr_list = []
            year_price_data = {}

            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = h5f[_prefixed(bp, "detailed_results")][sc_key]
                yr_list.append(year)
                prices = None
                for pk in ["electricity_prices", "nodal_electricity_prices"]:
                    if pk in sc:
                        p = sc[pk][:]
                        prices = p.mean(axis=0) if p.ndim == 2 else p
                        break
                if prices is not None:
                    year_price_data[year] = prices

        if not yr_list:
            return _empty_figure("No price data")

        days_per_year = 365
        n_years = len(yr_list)
        cost_matrix = np.zeros((days_per_year, n_years))
        for i, year in enumerate(yr_list):
            prices = year_price_data.get(year, np.array([]))
            if len(prices) > 0:
                timesteps = HOURS_STD_YEAR // tres
                if len(prices) < timesteps:
                    factor = max(1, timesteps // len(prices))
                    prices = np.repeat(prices, factor)[:timesteps]
                for day in range(days_per_year):
                    st = day * spd
                    en = min((day + 1) * spd, len(prices))
                    if st < len(prices):
                        dp = prices[st:en]
                        valid = dp[dp > 0]
                        if len(valid) > 0:
                            cost_matrix[day, i] = np.mean(valid)

        if self._sigma > 0:
            from scipy.ndimage import gaussian_filter1d
            cost_matrix = gaussian_filter1d(cost_matrix, sigma=self._sigma * 2, axis=0)

        return _heatmap_figure(
            cost_matrix, x_labels=[str(y) for y in yr_list],
            y_labels=[str(d) for d in range(365)],
            title="Daily Electricity Price Evolution",
            colorbar_title="$/MWh", palette="Turbo256",
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 10 — Inter-Node Flows
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class InterNodeFlowsChart(BokehChart):
    TITLE = "Inter-Node Flows"
    TR_KEY = "results_charts.inter_node_flows"

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> Any:
        yr_list = []
        imports_by_yr: dict[int, dict[int, float]] = {}
        exports_by_yr: dict[int, dict[int, float]] = {}
        num_nodes = 0
        node_names = []

        with h5py.File(h5_path, "r") as h5f:
            bp = kw.get("base_prefix", "")
            tres = _get_temporal_res(h5f)
            node_names = _get_node_names(h5f, bp)
            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = h5f[_prefixed(bp, "detailed_results")][sc_key]
                if "power_flow" not in sc:
                    continue
                pf = sc["power_flow"][:]
                if pf.ndim != 3:
                    continue
                n = pf.shape[0]
                num_nodes = max(num_nodes, n)
                yr_list.append(year)
                imp, exp = {}, {}
                for node in range(n):
                    imp_val, exp_val = 0.0, 0.0
                    for other in range(n):
                        if other == node:
                            continue
                        imp_val += np.maximum(pf[other, node, :], 0).sum()
                        exp_val += np.maximum(pf[node, other, :], 0).sum()
                    imp[node] = imp_val * tres / 1e3
                    exp[node] = exp_val * tres / 1e3
                imports_by_yr[year] = imp
                exports_by_yr[year] = exp

        if not yr_list:
            return _empty_figure("No power flow data")

        p = _themed_figure(
            title="Inter-Node Power Flows (Imports + / Exports -)",
            height=500,
        )
        p.yaxis.axis_label = "Energy (GWh)"

        colors = get_tab10()
        bottom_pos = np.zeros(len(yr_list))
        bottom_neg = np.zeros(len(yr_list))
        for node in range(num_nodes):
            label = node_names[node] if node < len(node_names) else f"Node {node}"
            color = colors[node % len(colors)]
            imp_vals = np.array([imports_by_yr.get(y, {}).get(node, 0) for y in yr_list])
            exp_vals = np.array([exports_by_yr.get(y, {}).get(node, 0) for y in yr_list])

            p.vbar(x=yr_list, top=(bottom_pos + imp_vals).tolist(),
                   bottom=bottom_pos.tolist(), width=0.7,
                   fill_color=color, fill_alpha=0.85, legend_label=f"{label} (imp)")
            bottom_pos = bottom_pos + imp_vals

            p.vbar(x=yr_list, top=bottom_neg.tolist(),
                   bottom=(bottom_neg - exp_vals).tolist(), width=0.7,
                   fill_color=color, fill_alpha=0.4,
                   legend_label=f"{label} (exp)")
            bottom_neg = bottom_neg - exp_vals

        _style_legend(p)
        return p


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 11 — MGA
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MGAComparisonChart(BokehChart):
    TITLE = "MGA"
    TR_KEY = "results_charts.mga_comparison"

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> Any:
        with h5py.File(h5_path, "r") as f:
            if "mga" not in f:
                return _empty_figure("No MGA results available.\nEnable MGA and re-run.")

            mga = f["mga"]
            num_alts = int(mga.attrs.get("num_alternatives", 0))
            slack = float(mga.attrs.get("slack_fraction", 0))
            optimal_cost = float(mga.attrs.get("optimal_cost", 0))
            mga_years = mga.attrs.get("years", [])
            if hasattr(mga_years, 'tolist'):
                mga_years = mga_years.tolist()

            gen_names = []
            if "generator_names" in mga:
                gen_names = [n.decode() if isinstance(n, bytes) else str(n) for n in mga["generator_names"][:]]
            bat_names = []
            if "battery_names" in mga:
                bat_names = [n.decode() if isinstance(n, bytes) else str(n) for n in mga["battery_names"][:]]

            tech_names = gen_names + [f"{b} (bat)" for b in bat_names]
            alt_ids, alt_costs, alt_diversity = [], [], []
            alt_gen_inv, alt_bat_inv, alt_re_pen = [], [], []

            for k in range(num_alts):
                grp_key = f"alternative_{k}"
                if grp_key not in mga:
                    continue
                grp = mga[grp_key]
                alt_ids.append(k)
                alt_costs.append(float(grp.attrs.get("cost", 0)))
                div = grp.attrs.get("diversity_objective", None)
                alt_diversity.append(float(div) if div is not None else 0.0)
                if "gen_investment" in grp:
                    alt_gen_inv.append(grp["gen_investment"][:].sum(axis=(0, 2)))
                else:
                    alt_gen_inv.append(np.zeros(len(gen_names)))
                if "bat_power_investment" in grp:
                    alt_bat_inv.append(grp["bat_power_investment"][:].sum(axis=(0, 2)))
                else:
                    alt_bat_inv.append(np.zeros(len(bat_names)))
                if "re_penetration" in grp:
                    alt_re_pen.append(grp["re_penetration"][:])
                else:
                    alt_re_pen.append(np.zeros(len(mga_years)))

        if not alt_ids:
            return _empty_figure("No alternatives found")

        colors = get_tab10()
        n_alts = len(alt_ids)
        inv_matrix = np.array([np.concatenate([alt_gen_inv[i], alt_bat_inv[i]]) for i in range(n_alts)])
        active = inv_matrix.sum(axis=0) > 0.01
        active_names = [tech_names[j] for j in range(len(tech_names)) if j < len(active) and active[j]]
        active_inv = inv_matrix[:, :len(active)][:, active]

        # Investment portfolio
        p1 = _themed_figure(title="Investment Portfolio", height=350,
                            x_range=active_names if active_names else ["(none)"])
        p1.yaxis.axis_label = "Investment (MW)"
        p1.xaxis.major_label_orientation = 0.7
        n_techs = len(active_names)
        bar_width = 0.7 / max(1, n_alts)
        for i in range(n_alts):
            label = "Optimal" if alt_ids[i] == 0 else f"Alt {alt_ids[i]}"
            offset = (i - n_alts / 2 + 0.5) * bar_width
            x_pos = [j + offset for j in range(n_techs)]
            vals = active_inv[i].tolist() if n_techs > 0 else []
            p1.vbar(x=x_pos, top=vals, width=bar_width * 0.9,
                    fill_color=colors[i % len(colors)], fill_alpha=0.85,
                    legend_label=label)

        # Cost vs Diversity
        p2 = _themed_figure(title="Cost vs Diversity", height=350)
        p2.xaxis.axis_label = "Diversity"
        p2.yaxis.axis_label = "Cost ($M)"
        for i in range(n_alts):
            marker = "star" if alt_ids[i] == 0 else "circle"
            sz = 15 if alt_ids[i] == 0 else 10
            p2.scatter([alt_diversity[i]], [alt_costs[i] / 1e6],
                       marker=marker, size=sz,
                       fill_color=colors[i % len(colors)],
                       legend_label="Optimal" if alt_ids[i] == 0 else f"Alt {alt_ids[i]}")
        if optimal_cost > 0 and slack > 0:
            limit = optimal_cost * (1 + slack) / 1e6
            p2.add_layout(Span(location=limit, dimension="width",
                               line_dash="dashed", line_color="gray"))

        # RE penetration trajectories
        p3 = _themed_figure(title="RE Penetration Trajectories", height=350)
        p3.yaxis.axis_label = "RE %"
        p3.xaxis.axis_label = "Year"
        for i in range(n_alts):
            rp = alt_re_pen[i]
            yrs = mga_years[:len(rp)]
            dash = "solid" if alt_ids[i] == 0 else "dashed"
            lw = 2.5 if alt_ids[i] == 0 else 1.5
            p3.line(list(yrs), (rp * 100).tolist(), line_color=colors[i % len(colors)],
                    line_width=lw, line_dash=dash,
                    legend_label="Optimal" if alt_ids[i] == 0 else f"Alt {alt_ids[i]}")

        for p in (p1, p2, p3):
            _style_legend(p)

        return gridplot([[p1, p2], [p3, None]], sizing_mode="stretch_both")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 12 — Fuel Supply
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FuelSupplyChart(BokehChart):
    TITLE = "Fuel Supply"
    TR_KEY = "results_charts.fuel_supply"

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> Any:
        year_supply: dict[int, dict[str, float]] = {}
        year_demand: dict[int, dict[str, float]] = {}
        year_loss: dict[int, dict[str, float]] = {}
        all_fuels: set[str] = set()

        with h5py.File(h5_path, "r") as h5f:
            bp = kw.get("base_prefix", "")
            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = h5f[_prefixed(bp, "detailed_results")][sc_key]
                if "primary_energy" not in sc:
                    continue
                pe = sc["primary_energy"]
                supply = {}
                if "total_fuel_supply" in pe:
                    for fuel in pe["total_fuel_supply"]:
                        val = float(np.sum(pe["total_fuel_supply"][fuel][:]))
                        supply[fuel] = val
                        all_fuels.add(fuel)
                year_supply[year] = supply
                demand = {}
                if "total_ne_demand_satisfied" in pe:
                    for fuel in pe["total_ne_demand_satisfied"]:
                        val = float(np.sum(pe["total_ne_demand_satisfied"][fuel][:]))
                        demand[fuel] = val
                        all_fuels.add(fuel)
                year_demand[year] = demand
                loss = {}
                if "total_loss_of_supply" in pe:
                    for fuel in pe["total_loss_of_supply"]:
                        val = float(np.sum(pe["total_loss_of_supply"][fuel][:]))
                        loss[fuel] = val
                year_loss[year] = loss

        if not year_supply:
            return _empty_figure("No primary energy data")

        sorted_years = sorted(year_supply.keys())
        fuel_list = sorted(all_fuels)
        fuel_colors = {f: _FUEL_COLORS[i % len(_FUEL_COLORS)] for i, f in enumerate(fuel_list)}

        p1 = _themed_figure(title="Fuel Supply by Year", height=350)
        p1.yaxis.axis_label = "Fuel Supply"
        bottom = np.zeros(len(sorted_years))
        for fuel in fuel_list:
            vals = np.array([year_supply.get(y, {}).get(fuel, 0.0) for y in sorted_years])
            p1.vbar(x=sorted_years, top=(bottom + vals).tolist(), bottom=bottom.tolist(),
                    width=0.7, fill_color=fuel_colors[fuel], legend_label=fuel)
            bottom = bottom + vals

        p2 = _themed_figure(title="Demand Satisfied + Loss of Supply", height=350)
        p2.yaxis.axis_label = "Demand Satisfied"
        bottom = np.zeros(len(sorted_years))
        for fuel in fuel_list:
            vals = np.array([year_demand.get(y, {}).get(fuel, 0.0) for y in sorted_years])
            p2.vbar(x=sorted_years, top=(bottom + vals).tolist(), bottom=bottom.tolist(),
                    width=0.7, fill_color=fuel_colors[fuel], legend_label=fuel)
            bottom = bottom + vals

        total_loss_vals = [sum(year_loss.get(y, {}).values()) for y in sorted_years]
        if any(v > 0 for v in total_loss_vals):
            p2.vbar(x=sorted_years, top=(bottom + np.array(total_loss_vals)).tolist(),
                    bottom=bottom.tolist(), width=0.7,
                    fill_color="#e74c3c", fill_alpha=0.5, hatch_pattern="/",
                    legend_label="Loss of Supply")

        for p in (p1, p2):
            _style_legend(p)

        return column(p1, p2, sizing_mode="stretch_both")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 13 — Fuel Costs
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FuelCostChart(BokehChart):
    TITLE = "Fuel Costs"
    TR_KEY = "results_charts.fuel_costs"

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> Any:
        cost_data: dict[int, dict[str, float]] = {}

        with h5py.File(h5_path, "r") as h5f:
            bp = kw.get("base_prefix", "")
            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = h5f[_prefixed(bp, "detailed_results")][sc_key]
                if "primary_energy" not in sc:
                    continue
                pe = sc["primary_energy"]
                costs = {}
                for key in ("total_fuel_cost", "total_transport_cost", "total_loss_penalty"):
                    costs[key] = float(pe.attrs.get(key, 0.0))
                cost_data[year] = costs

        if not cost_data:
            return _empty_figure("No primary energy cost data")

        sorted_years = sorted(cost_data.keys())

        p1 = _themed_figure(title="Cost Breakdown by Year", height=350)
        p1.yaxis.axis_label = "Cost (k$)"
        cost_items = [
            ("total_fuel_cost", "Fuel Cost", "#3498db"),
            ("total_transport_cost", "Transport Cost", "#e67e22"),
            ("total_loss_penalty", "Loss Penalty", "#e74c3c"),
        ]
        bottom = np.zeros(len(sorted_years))
        for key, label, color in cost_items:
            vals = np.array([cost_data[y][key] / 1e3 for y in sorted_years])
            p1.vbar(x=sorted_years, top=(bottom + vals).tolist(), bottom=bottom.tolist(),
                    width=0.7, fill_color=color, legend_label=label)
            bottom = bottom + vals

        p2 = _themed_figure(title="Total PE Cost Evolution", height=250)
        p2.xaxis.axis_label = "Year"
        p2.yaxis.axis_label = "Total PE Cost (k$)"
        total_costs = [sum(cost_data[y].values()) / 1e3 for y in sorted_years]
        p2.line(sorted_years, total_costs, line_color="#2c3e50", line_width=2,
                legend_label="Total PE Cost")
        p2.scatter(sorted_years, total_costs, fill_color="#2c3e50", size=6)

        for p in (p1, p2):
            _style_legend(p)

        return column(p1, p2, sizing_mode="stretch_both")


# ──────────────────────────────────────────────────────────────
# Heatmap helper
# ──────────────────────────────────────────────────────────────

def _heatmap_figure(
    data: np.ndarray,
    x_labels: list[str],
    y_labels: list[str],
    title: str = "",
    colorbar_title: str = "",
    palette: str = "Turbo256",
    x_axis_label: str = "",
) -> figure:
    """Create a heatmap figure using rect glyphs with a color mapper."""
    palette_list = {
        "Turbo256": Turbo256,
        "RdBu11": list(reversed(RdBu11)),
    }.get(palette, Turbo256)

    vmin = float(np.nanmin(data)) if data.size > 0 else 0
    vmax = float(np.nanmax(data)) if data.size > 0 else 1
    if vmin == vmax:
        vmax = vmin + 1

    mapper = LinearColorMapper(palette=palette_list, low=vmin, high=vmax)

    p = _themed_figure(
        title=title, height=400,
        x_range=x_labels, y_range=y_labels,
    )
    if x_axis_label:
        p.xaxis.axis_label = x_axis_label

    # Build rect data
    xs, ys, vals = [], [], []
    n_rows, n_cols = data.shape
    for r in range(n_rows):
        for c in range(n_cols):
            if r < len(y_labels) and c < len(x_labels):
                xs.append(x_labels[c])
                ys.append(y_labels[r])
                v = data[r, c]
                vals.append(float(v) if np.isfinite(v) else 0.0)

    source = ColumnDataSource(data=dict(x=xs, y=ys, values=vals))
    p.rect(x="x", y="y", width=1, height=1, source=source,
           fill_color={"field": "values", "transform": mapper},
           line_color=None)

    color_bar = ColorBar(color_mapper=mapper, title=colorbar_title,
                         ticker=BasicTicker())
    p.add_layout(color_bar, "right")

    p.add_tools(HoverTool(tooltips=[
        ("X", "@x"), ("Y", "@y"), ("Value", "@values{0.0}"),
    ]))

    return p


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Registry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BOKEH_CHART_CLASSES = [
    GenerationMixChart,
    DemandCoverageChart,
    BatteryHeatmapChart,
    BatteryOperationChart,
    GenerationBySourceChart,
    ScenariosComparisonChart,
    NetLoadHeatmapChart,
    CFLcoeVallcoeChart,
    ElectricityCostChart,
    InterNodeFlowsChart,
    MGAComparisonChart,
    FuelSupplyChart,
    FuelCostChart,
]
