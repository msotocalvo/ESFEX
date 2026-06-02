"""Plotly-based interactive chart widgets for results visualization.

Mirrors the 13 chart types from results_charts.py but uses Plotly for
interactive, professional charts rendered in QWebEngineView.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import h5py
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

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

# Reuse ALL data loading helpers from the matplotlib module
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

_FUEL_COLORS = [
    "#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6",
    "#1abc9c", "#e67e22", "#34495e", "#d35400", "#7f8c8d",
]

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ──────────────────────────────────────────────────────────────
# Plotly theme from ESFEX theme
# ──────────────────────────────────────────────────────────────

def _plotly_layout_defaults() -> dict:
    """Return Plotly layout kwargs matching the active ESFEX theme."""
    theme = current_theme()
    c = theme.colors
    t = theme.typography
    return dict(
        paper_bgcolor=c.surface_primary,
        plot_bgcolor=c.surface_primary,
        font=dict(family=t.family_ui, color=c.text_primary, size=12),
        xaxis=dict(gridcolor=c.border_light, linecolor=c.border_light,
                   zerolinecolor=c.border_medium),
        yaxis=dict(gridcolor=c.border_light, linecolor=c.border_light,
                   zerolinecolor=c.border_medium),
        legend=dict(bgcolor="rgba(255,255,255,0.7)", bordercolor=c.border_light,
                    borderwidth=1, font=dict(size=10)),
        margin=dict(l=60, r=40, t=50, b=50),
        hovermode="closest",
    )


def _apply_theme(fig: go.Figure):
    """Apply ESFEX theme to a Plotly figure."""
    fig.update_layout(**_plotly_layout_defaults())


# ──────────────────────────────────────────────────────────────
# Base class
# ──────────────────────────────────────────────────────────────

class PlotlyChart:
    """Base class for Plotly-based charts."""

    TITLE = "Chart"
    TR_KEY = ""

    def build_figure(self, h5_path: Path, years: list[int], **kwargs) -> go.Figure:
        """Build and return a Plotly figure.  Subclasses must override."""
        raise NotImplementedError

    def get_params_widget(self) -> Optional[QWidget]:
        """Override to return a Qt controls widget for chart parameters."""
        return None

    def safe_build(self, h5_path: Path, years: list[int], **kwargs) -> go.Figure:
        """Build figure with error handling."""
        try:
            fig = self.build_figure(h5_path, years, **kwargs)
            _apply_theme(fig)
            return fig
        except Exception as e:
            logger.exception(f"Chart {self.TITLE} failed: {e}")
            fig = go.Figure()
            fig.add_annotation(
                text=f"Error building {self.TITLE}:<br>{e}",
                xref="paper", yref="paper",
                x=0.5, y=0.5, showarrow=False,
                font=dict(size=14, color="red"),
            )
            _apply_theme(fig)
            return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 1 — Generation Mix (stacked area + investments bar)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class GenerationMixChart(PlotlyChart):
    TITLE = "Generation Mix"
    TR_KEY = "results_charts.gen_mix"

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> go.Figure:
        fig = make_subplots(
            rows=2, cols=1, row_heights=[0.65, 0.35],
            vertical_spacing=0.12,
            specs=[[{"secondary_y": True}], [{"secondary_y": True}]],
            subplot_titles=[
                "a) Generation Mix Evolution",
                "b) Annual Capacity Investments & Retirements",
            ],
        )

        with h5py.File(h5_path, "r") as h5f:
            bp = kw.get("base_prefix", "")
            tres = _get_temporal_res(h5f)
            gen_configs = _load_gen_configs(h5f, bp)
            bat_configs = _load_bat_configs(h5f, bp)
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

                bat_spill = _load_bat_data(sc, "battery_spillage")
                if bat_spill:
                    year_spill = None
                    for _, arr in bat_spill.items():
                        t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                        m = np.array(_aggregate(t, "monthly", tres))
                        if year_spill is None:
                            year_spill = m
                        else:
                            ml = min(len(year_spill), len(m))
                            year_spill[:ml] += m[:ml]
                    if year_spill is not None:
                        all_gen.setdefault("Battery spillage", []).extend(year_spill.tolist())

                for ev_key, ev_label in [("EV_V2G", "V2G discharge"),
                                         ("EV_charging", "V2G charge")]:
                    if ev_key in sc:
                        ev_arr = sc[ev_key][:]
                        t = _sum_nodes(ev_arr) if ev_arr.ndim >= 2 else ev_arr
                        m = _aggregate(t, "monthly", tres)
                        all_gen.setdefault(ev_label, []).extend(m.tolist())

                if "curtailment" in sc:
                    curt = sc["curtailment"][:]
                    ct = _sum_nodes(curt) if curt.ndim >= 2 else curt
                    cm = _aggregate(ct, "monthly", tres)
                    all_gen.setdefault("Curtailment", []).extend(cm.tolist())

                for rkey, rlabel in [("reserve_dynamic", "Dynamic reserve"),
                                     ("reserve_static", "Static reserve")]:
                    if rkey in sc:
                        rd = sc[rkey][:]
                        rt = _sum_nodes(rd) if rd.ndim >= 2 else rd
                        rm = _aggregate(rt, "monthly", tres)
                        all_gen.setdefault(rlabel, []).extend(rm.tolist())

                if "demand" in sc:
                    dem = sc["demand"][:]
                    dt = _sum_nodes(dem) if dem.ndim >= 2 else dem
                    dm = _aggregate(dt, "monthly", tres)
                    demand_all.extend(dm.tolist())
                    months_this_year = max(months_this_year, len(dm))

                if "rooftop_generation" in sc:
                    rg = sc["rooftop_generation"][:]
                    rgt = _sum_nodes(rg) if rg.ndim >= 2 else rg
                    rgm = _aggregate(rgt, "monthly", tres)
                    all_gen.setdefault("Solar rooftop", []).extend(rgm.tolist())

                if months_this_year == 0:
                    months_this_year = 12
                total_months += months_this_year

                # Investment data
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
                    gen_invs = inv_data["gen_investment_power"]
                    for gi, inv_mw in enumerate(gen_invs):
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
                    bat_invs = inv_data["bat_investment_power"]
                    for bi, inv_mw in enumerate(bat_invs):
                        if inv_mw > 0 and bi < len(bat_configs):
                            bc = bat_configs[bi]
                            bn = bc.get("name", f"Battery_{bi}")
                            if isinstance(bn, bytes):
                                bn = bn.decode()
                            bn_canon, _ = _canonical_tech_name(bn)
                            if bn_canon not in bat_inv_by_tech:
                                bat_inv_by_tech[bn_canon] = np.zeros(len(scenarios))
                            bat_inv_by_tech[bn_canon][year_idx] += float(inv_mw) / 1000
                            if "invest_cost" in bc:
                                year_cost += float(inv_mw) * _parse_invest_cost(bc["invest_cost"]) / 1e6

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
            fig.add_annotation(text="No generation data", xref="paper", yref="paper",
                               x=0.5, y=0.5, showarrow=False, font=dict(size=14))
            return fig

        # Pad shorter series
        for key in all_gen:
            arr = np.array(all_gen[key])
            if len(arr) < total_months:
                arr = np.pad(arr, (0, total_months - len(arr)), mode="constant")
            all_gen[key] = arr

        demand_array = np.array(demand_all) if demand_all else None
        if demand_array is not None and len(demand_array) < total_months:
            demand_array = np.pad(demand_array, (0, total_months - len(demand_array)), mode="constant")

        cats = _categorize_gen_names(list(all_gen.keys()))

        # Aggregate categories with many individual generators into single
        # traces to keep the total trace count low (Plotly stacked areas
        # become very slow with > ~15 traces).
        _MAX_TRACES_PER_CAT = 6
        merged_gen: dict[str, np.ndarray] = {}
        merged_order: list[tuple[str, str]] = []  # (cat_key, label)

        for cat_key in ("renewable", "rooftop", "thermal", "storage_discharge",
                         "storage_charge", "curtailment", "spillage", "reserve"):
            members = [t for t in cats[cat_key] if t in all_gen and np.any(all_gen[t] > 0)]
            if len(members) <= _MAX_TRACES_PER_CAT:
                for t in members:
                    merged_gen[t] = all_gen[t]
                    merged_order.append((cat_key, t))
            else:
                # Sum all members into one aggregate trace
                agg_label = {
                    "thermal": "Thermal",
                    "renewable": "Renewable",
                    "storage_discharge": "Battery discharge",
                    "storage_charge": "Battery charge",
                }.get(cat_key, cat_key.replace("_", " ").title())
                total = np.zeros(total_months)
                for t in members:
                    total += all_gen[t]
                merged_gen[agg_label] = total
                merged_order.append((cat_key, agg_label))

        # Build x-axis labels
        x_labels = []
        for i, yr in enumerate(year_list):
            for m in range(12):
                x_labels.append(f"{yr}-{m+1:02d}")
        x_labels = x_labels[:total_months]

        # Positive stacked area (row=1)
        pos_cats = {"renewable", "rooftop", "thermal", "storage_discharge"}
        for cat_key, label in merged_order:
            if cat_key in pos_cats:
                fig.add_trace(go.Scatter(
                    x=x_labels, y=merged_gen[label] / 1000,
                    name=label, mode="lines",
                    line=dict(width=0), fillcolor=_color_for(label),
                    stackgroup="pos",
                    hovertemplate="%{y:.1f} GWh",
                ), row=1, col=1, secondary_y=False)

        # Negative stacked area
        neg_cats = {"storage_charge", "curtailment", "spillage", "reserve"}
        for cat_key, label in merged_order:
            if cat_key in neg_cats:
                fig.add_trace(go.Scatter(
                    x=x_labels, y=-merged_gen[label] / 1000,
                    name=label, mode="lines",
                    line=dict(width=0), fillcolor=_color_for(label),
                    stackgroup="neg",
                    hovertemplate="%{y:.1f} GWh",
                ), row=1, col=1, secondary_y=False)

        # Demand line
        if demand_array is not None:
            fig.add_trace(go.Scatter(
                x=x_labels, y=demand_array / 1000,
                name="Total Demand", mode="lines",
                line=dict(color="black", width=2.5, dash="dash"),
                hovertemplate="%{y:.1f} GWh",
            ), row=1, col=1, secondary_y=False)

        # RE penetration on secondary y
        renewable_total = sum(all_gen.get(t, 0) for t in cats["renewable"])
        thermal_total = sum(all_gen.get(t, 0) for t in cats["thermal"])
        total_gen_arr = renewable_total + thermal_total
        re_pen = np.divide(renewable_total, total_gen_arr,
                           out=np.zeros(total_months, dtype=float),
                           where=(total_gen_arr if isinstance(total_gen_arr, np.ndarray)
                                  else np.zeros(total_months)) != 0) * 100

        fig.add_trace(go.Scatter(
            x=x_labels, y=re_pen,
            name="Operational RE (%)", mode="lines",
            line=dict(color="red", width=2.5, dash="dash"),
            hovertemplate="%{y:.1f}%",
        ), row=1, col=1, secondary_y=True)

        # RE target
        if master_re_targets and any(t > 0 for t in master_re_targets):
            re_target_monthly = []
            for t_val in master_re_targets:
                re_target_monthly.extend([t_val] * 12)
            re_target_arr = np.array(re_target_monthly[:total_months])
            fig.add_trace(go.Scatter(
                x=x_labels[:len(re_target_arr)], y=re_target_arr,
                name="RE Target (%)", mode="lines",
                line=dict(color="darkblue", width=2),
            ), row=1, col=1, secondary_y=True)

        fig.update_yaxes(title_text="Energy (GWh)", row=1, col=1, secondary_y=False)
        fig.update_yaxes(title_text="RE Penetration (%)", row=1, col=1,
                         secondary_y=True, range=[0, 105],
                         tickvals=[0, 20, 40, 60, 80, 100])

        # ── Subplot b: Investments & Retirements ──
        if year_list:
            n_years = len(year_list)

            # Aggregate investments by canonical category (Solar, Wind, etc.)
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
            # Merge all thermal investments into one bar
            thermal_inv = np.zeros(n_years)
            for tech in cats["thermal"]:
                if tech in gen_inv_by_tech and np.any(gen_inv_by_tech[tech] > 0):
                    thermal_inv += gen_inv_by_tech[tech][:n_years]
            if np.any(thermal_inv > 0):
                inv_agg["Thermal"] = thermal_inv

            for label, vals in inv_agg.items():
                fig.add_trace(go.Bar(
                    x=[str(y) for y in year_list], y=vals,
                    name=f"{label} (inv)",
                    marker_color=_color_for(label), opacity=0.85,
                ), row=2, col=1, secondary_y=False)

            # Aggregate retirements by canonical category
            ret_agg: dict[str, np.ndarray] = {}
            for tech_name, ret_vals in ret_by_tech.items():
                if np.any(ret_vals > 0):
                    # Map to a canonical label
                    canon, _ = _canonical_tech_name(tech_name)
                    is_re = _is_renewable(canon) or _is_renewable(tech_name)
                    label = canon if is_re else "Thermal"
                    ret_agg.setdefault(label, np.zeros(n_years))
                    ret_agg[label] += ret_vals[:n_years]

            for label, vals in ret_agg.items():
                fig.add_trace(go.Bar(
                    x=[str(y) for y in year_list], y=-vals,
                    name=f"{label} (retired)",
                    marker_color=_color_for(label), opacity=0.5,
                    marker_pattern_shape="/",
                ), row=2, col=1, secondary_y=False)

            # Investment cost line
            cost_arr = np.array(total_cost_by_year[:n_years])
            fig.add_trace(go.Scatter(
                x=[str(y) for y in year_list], y=cost_arr,
                name="Investment Cost (M$)", mode="lines+markers",
                line=dict(color="green", width=2.5),
                marker=dict(size=5),
            ), row=2, col=1, secondary_y=True)

            fig.update_yaxes(title_text="Capacity (GW)", row=2, col=1, secondary_y=False)
            fig.update_yaxes(title_text="Investment Cost (M$)", row=2, col=1, secondary_y=True)

        fig.update_layout(barmode="relative", height=900,
                          legend=dict(orientation="h", y=-0.08, x=0.5, xanchor="center"))
        return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 2 — Demand Coverage Detail
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DemandCoverageChart(PlotlyChart):
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
        lay.addWidget(QLabel(tr("results_charts.days")))
        self._ndays_spin = QSpinBox()
        self._ndays_spin.setRange(1, 30)
        self._ndays_spin.setValue(7)
        self._ndays_spin.valueChanged.connect(lambda v: setattr(self, "_num_days", v))
        lay.addWidget(self._ndays_spin)
        lay.addStretch()
        return w

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> go.Figure:
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        year_idx = kw.get("year_idx", self._year_idx)

        with h5py.File(h5_path, "r") as h5f:
            bp = kw.get("base_prefix", "")
            tres = _get_temporal_res(h5f)
            scenarios = list(_sorted_scenarios(h5f, bp))
            if year_idx >= len(scenarios):
                fig.add_annotation(text="No data", xref="paper", yref="paper",
                                   x=0.5, y=0.5, showarrow=False)
                return fig

            sc_key, year = scenarios[year_idx]
            sc = h5f[_prefixed(bp, "detailed_results")][sc_key]

            hpd = max(1, 24 // tres)
            start_h = self._start_day * hpd
            end_h = min(start_h + self._num_days * hpd, HOURS_STD_YEAR // tres)
            num_hours = end_h - start_h

            gen_data = _load_gen_data(sc)
            generation: dict[str, np.ndarray] = {}
            for name, arr in gen_data.items():
                total = _sum_nodes(arr) if arr.ndim >= 2 else arr
                slc = total[start_h:end_h]
                if np.any(slc > 0):
                    generation[name] = slc

            bat_discharge = _load_bat_data(sc, "battery_discharge")
            bat_dch_total = np.zeros(num_hours)
            for _, arr in bat_discharge.items():
                t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                slc = t[start_h:end_h]
                ml = min(len(bat_dch_total), len(slc))
                bat_dch_total[:ml] += slc[:ml]
            if np.any(bat_dch_total > 0):
                generation["Battery discharge"] = bat_dch_total

            cats = _categorize_gen_names(list(generation.keys()))
            time_hours = np.arange(num_hours) * tres

            # Aggregate categories with many generators
            _MAX = 8
            merged: dict[str, np.ndarray] = {}
            merged_order: list[tuple[str, str]] = []
            for cat_key in ("renewable", "thermal", "storage_discharge"):
                members = [t for t in cats[cat_key] if t in generation]
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

            # Positive stacked area
            for cat_key, tech in merged_order:
                d = merged[tech].copy()
                if len(d) < num_hours:
                    d = np.pad(d, (0, num_hours - len(d)), mode="constant")
                fig.add_trace(go.Scatter(
                    x=time_hours, y=d, name=tech, mode="lines",
                    line=dict(width=0), fillcolor=_color_for(tech),
                    stackgroup="pos",
                    hovertemplate="%{y:.0f} MW",
                ), secondary_y=False)

            # Battery charge (negative)
            bat_charge = _load_bat_data(sc, "battery_charge")
            bat_ch_total = np.zeros(num_hours)
            for _, arr in bat_charge.items():
                t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                slc = t[start_h:end_h]
                ml = min(len(bat_ch_total), len(slc))
                bat_ch_total[:ml] += slc[:ml]
            if np.any(bat_ch_total > 0):
                fig.add_trace(go.Scatter(
                    x=time_hours, y=-bat_ch_total,
                    name="Battery charge", mode="lines",
                    line=dict(width=0), fillcolor=_color_for("Battery charge"),
                    fill="tozeroy",
                ), secondary_y=False)

            # Demand line
            if "demand" in sc:
                dem = sc["demand"][:]
                dt = _sum_nodes(dem) if dem.ndim >= 2 else dem
                demand_data = dt[start_h:end_h]
                fig.add_trace(go.Scatter(
                    x=time_hours[:len(demand_data)], y=demand_data,
                    name="Demand", mode="lines",
                    line=dict(color="black", width=2.5),
                ), secondary_y=False)

            # Battery SOC on secondary axis
            soc_total = None
            bat_soc = _load_bat_data(sc, "battery_soc")
            for _, arr in bat_soc.items():
                t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                slc = t[start_h:end_h]
                if soc_total is None:
                    soc_total = slc.copy()
                else:
                    ml = min(len(soc_total), len(slc))
                    soc_total[:ml] += slc[:ml]
            if soc_total is not None:
                fig.add_trace(go.Scatter(
                    x=time_hours[:len(soc_total)], y=soc_total,
                    name="Battery SOC", mode="lines",
                    line=dict(color="blue", width=2, dash="dash"),
                ), secondary_y=True)
                fig.update_yaxes(title_text="Battery SOC (MWh)", secondary_y=True)

        end_day = min(self._start_day + self._num_days, 365)
        fig.update_layout(
            title=f"Demand Coverage — Year {year} (Days {self._start_day + 1}-{end_day})",
            height=600,
        )
        fig.update_xaxes(title_text="Hour")
        fig.update_yaxes(title_text="Power (MW)", secondary_y=False)
        return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 3 — Battery Heatmap
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BatteryHeatmapChart(PlotlyChart):
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

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> go.Figure:
        rows = []
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
                    # Pad to 12 months if shorter (rolling horizon overlap)
                    padded = np.zeros(12)
                    padded[:len(monthly)] = monthly[:12]
                    rows.append(padded)
                    y_labels.append(str(year))

        fig = go.Figure()
        if not rows:
            fig.add_annotation(text="No battery data", xref="paper", yref="paper",
                               x=0.5, y=0.5, showarrow=False)
            return fig

        data = np.array(rows).T  # [12 months × years]
        if self._sigma > 0:
            from scipy.ndimage import gaussian_filter
            data = gaussian_filter(data, sigma=self._sigma)

        fig.add_trace(go.Heatmap(
            z=data, x=y_labels, y=MONTHS,
            colorscale="Turbo", colorbar=dict(title="MWh"),
            hovertemplate="Year: %{x}<br>Month: %{y}<br>Net Flow: %{z:.0f} MWh<extra></extra>",
        ))
        fig.update_layout(
            title="Monthly Net Battery Flow (Charge - Discharge)",
            height=500,
        )
        return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 4 — Battery Operation (diverging bar)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BatteryOperationChart(PlotlyChart):
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

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> go.Figure:
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

        c_agg = _aggregate(charge_total, self._resolution, tres) / 1e3
        d_agg = _aggregate(discharge_total, self._resolution, tres) / 1e3

        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=c_agg, name="Charge", marker_color="#9B59B6", opacity=0.85,
        ))
        fig.add_trace(go.Bar(
            y=-d_agg, name="Discharge", marker_color="#3498DB", opacity=0.85,
        ))
        fig.update_layout(
            title=f"Battery Operation — Year {year} ({self._resolution})",
            xaxis_title=self._resolution.capitalize(),
            yaxis_title="Energy (GWh)",
            barmode="relative", height=500,
        )
        return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 5 — Generation by Source (multi-line)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class GenerationBySourceChart(PlotlyChart):
    TITLE = "Generation by Source"
    TR_KEY = "results_charts.gen_by_source"

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> go.Figure:
        fig = make_subplots(rows=2, cols=1, vertical_spacing=0.12,
                            subplot_titles=["Renewable Sources", "Thermal / Conventional"])

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
            fig.add_annotation(text="No data", xref="paper", yref="paper",
                               x=0.5, y=0.5, showarrow=False)
            return fig

        # Aggregate generators with same canonical name
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

        # Limit thermal to top N by total generation
        _MAX_LINES = 10
        if len(th_agg) > _MAX_LINES:
            sorted_th = sorted(th_agg.items(), key=lambda kv: kv[1].sum(), reverse=True)
            top = dict(sorted_th[:_MAX_LINES - 1])
            other = sum((v for _, v in sorted_th[_MAX_LINES - 1:]), np.zeros(n_years))
            if np.any(other > 0):
                top["Other thermal"] = other
            th_agg = top

        for name, vals in re_agg.items():
            fig.add_trace(go.Scatter(
                x=year_list[:len(vals)], y=vals.tolist(),
                name=name, mode="lines+markers",
                line=dict(color=_color_for(name), width=2),
                marker=dict(size=5),
            ), row=1, col=1)

        for name, vals in th_agg.items():
            fig.add_trace(go.Scatter(
                x=year_list[:len(vals)], y=vals.tolist(),
                name=name, mode="lines+markers",
                line=dict(color=_color_for(name), width=2),
                marker=dict(size=5),
            ), row=2, col=1)

        fig.update_yaxes(title_text="Annual Generation (GWh)", row=1, col=1)
        fig.update_yaxes(title_text="Annual Generation (GWh)", row=2, col=1)
        fig.update_xaxes(title_text="Year", row=2, col=1)
        fig.update_layout(height=700)
        return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 6 — Scenarios Comparison (RE% + total gen)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ScenariosComparisonChart(PlotlyChart):
    TITLE = "Scenarios Comparison"
    TR_KEY = "results_charts.scenarios_comparison"

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> go.Figure:
        fig = make_subplots(rows=1, cols=2,
                            subplot_titles=["RE Penetration Evolution", "Total Generation per Year"])

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
            fig.add_annotation(text="No data", xref="paper", yref="paper",
                               x=0.5, y=0.5, showarrow=False)
            return fig

        fig.add_trace(go.Scatter(
            x=yr_list, y=re_pct_list,
            name="RE %", mode="lines+markers",
            fill="tozeroy", fillcolor="rgba(39,174,96,0.2)",
            line=dict(color="#27ae60", width=2),
        ), row=1, col=1)

        fig.add_trace(go.Bar(
            x=yr_list, y=total_gwh_list,
            name="Total Gen", marker_color="#3498db", opacity=0.7,
        ), row=1, col=2)

        fig.update_yaxes(title_text="RE Penetration (%)", range=[0, 105], row=1, col=1)
        fig.update_yaxes(title_text="Total Generation (GWh)", row=1, col=2)
        fig.update_layout(height=450)
        return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 7 — Net Load Heatmap (month × hour-of-day)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class NetLoadHeatmapChart(PlotlyChart):
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

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> go.Figure:
        import pandas as pd
        fig = make_subplots(rows=1, cols=2,
                            subplot_titles=["Avg Net Load (MW)", "Avg Net Load Ramp (MW/h)"])

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
            fig.add_annotation(text="No data", xref="paper", yref="paper",
                               x=0.5, y=0.5, showarrow=False)
            return fig

        freq = f"{tres}h"
        start_yr = min(yr_list) if yr_list else 2025
        idx = pd.date_range(start=f"{start_yr}-01-01", periods=len(net_load_all), freq=freq)
        df = pd.DataFrame({"NL": net_load_all}, index=idx)
        df["ramp"] = df["NL"].diff()

        avg_nl = df.groupby([df.index.month, df.index.hour])["NL"].mean().unstack()
        avg_ramp = df.groupby([df.index.month, df.index.hour])["ramp"].mean().unstack()

        for i, (data, cscale) in enumerate([(avg_nl, "Jet"), (avg_ramp, "RdBu_r")]):
            vals = data.fillna(0).values
            if self._sigma > 0:
                from scipy.ndimage import gaussian_filter
                vals = gaussian_filter(vals, sigma=self._sigma)
            hour_labels = [int(h) for h in data.columns]
            month_labels = [MONTHS[m - 1] if 1 <= m <= 12 else str(m)
                            for m in data.index]
            fig.add_trace(go.Heatmap(
                z=vals, x=hour_labels, y=month_labels,
                colorscale=cscale,
                hovertemplate="Hour: %{x}<br>Month: %{y}<br>Value: %{z:.0f}<extra></extra>",
            ), row=1, col=i + 1)

        fig.update_xaxes(title_text="Hour of Day")
        fig.update_layout(height=500)
        return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 8 — CF / LCOE / VALLCOE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CFLcoeVallcoeChart(PlotlyChart):
    TITLE = "CF / LCOE / VALLCOE"
    TR_KEY = "results_charts.cf_lcoe_vallcoe"

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> go.Figure:
        fig = make_subplots(rows=2, cols=1, vertical_spacing=0.12, shared_xaxes=True,
                            subplot_titles=["Capacity Factors (%)", "LCOE / VALCOE ($/MWh)"])

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
                        if is_bat:
                            tech_label = f"Storage: {name.replace('_', ' ')}"
                        else:
                            tech_label, _ = _canonical_tech_name(name)
                        cf_arr = grp[name][:]
                        if cf_arr.ndim == 2:
                            for node in range(cf_arr.shape[0]):
                                cf_vals = cf_arr[node, :]
                                avg_cf = np.mean(cf_vals[cf_vals > 0]) * 100 if np.any(cf_vals > 0) else 0
                                avg_lcoe, avg_vallcoe = 0.0, 0.0
                                if lcoe_key in sc and name in sc[lcoe_key]:
                                    la = sc[lcoe_key][name][:]
                                    if la.ndim == 2:
                                        lv = la[node, :]
                                        avg_lcoe = np.mean(lv[lv > 0]) if np.any(lv > 0) else 0
                                if vallcoe_key in sc and name in sc[vallcoe_key]:
                                    va = sc[vallcoe_key][name][:]
                                    if va.ndim == 2:
                                        vv = va[node, :]
                                        avg_vallcoe = np.mean(vv[vv > 0]) if np.any(vv > 0) else 0
                                if avg_cf > 0:
                                    records.append(dict(tech=tech_label, year=year, node=node,
                                                        cf=avg_cf, lcoe=avg_lcoe, vallcoe=avg_vallcoe))
                        else:
                            avg_cf = np.mean(cf_arr[cf_arr > 0]) * 100 if np.any(cf_arr > 0) else 0
                            avg_lcoe, avg_vallcoe = 0.0, 0.0
                            if lcoe_key in sc and name in sc[lcoe_key]:
                                la = sc[lcoe_key][name][:]
                                avg_lcoe = np.mean(la[la > 0]) if np.any(la > 0) else 0
                            if vallcoe_key in sc and name in sc[vallcoe_key]:
                                va = sc[vallcoe_key][name][:]
                                avg_vallcoe = np.mean(va[va > 0]) if np.any(va > 0) else 0
                            if avg_cf > 0:
                                records.append(dict(tech=tech_label, year=year, node=0,
                                                    cf=avg_cf, lcoe=avg_lcoe, vallcoe=avg_vallcoe))

        if not records:
            fig.add_annotation(text="No CF/LCOE data", xref="paper", yref="paper",
                               x=0.5, y=0.5, showarrow=False)
            return fig

        techs = sorted(set(r["tech"] for r in records))
        yr_set = sorted(set(r["year"] for r in records))
        yr_min, yr_max = min(yr_set), max(yr_set)

        # CF box + scatter overlay (Box doesn't support per-point colorscale)
        colors = get_tab10()
        for i, tech in enumerate(techs):
            pts = [r for r in records if r["tech"] == tech]
            cfs = [p["cf"] for p in pts]
            c = colors[i % len(colors)]
            fig.add_trace(go.Box(
                x=[tech] * len(cfs), y=cfs,
                name=tech, boxpoints="all", jitter=0.4,
                marker=dict(size=4, color=c, opacity=0.6),
                line=dict(color=c),
                fillcolor="rgba(0,0,0,0.05)",
                showlegend=False,
            ), row=1, col=1)

        # LCOE scatter
        for tech in techs:
            pts = [r for r in records if r["tech"] == tech and r["lcoe"] > 0]
            if pts:
                fig.add_trace(go.Scatter(
                    x=[tech] * len(pts),
                    y=[p["lcoe"] for p in pts],
                    mode="markers", name=f"{tech} LCOE",
                    marker=dict(size=6, symbol="triangle-up",
                                color=[p["year"] for p in pts],
                                colorscale="RdYlBu_r", cmin=yr_min, cmax=yr_max),
                    showlegend=False,
                    hovertemplate="%{y:.1f} $/MWh<extra>LCOE</extra>",
                ), row=2, col=1)

            pts_v = [r for r in records if r["tech"] == tech and r["vallcoe"] > 0]
            if pts_v:
                fig.add_trace(go.Scatter(
                    x=[tech] * len(pts_v),
                    y=[p["vallcoe"] for p in pts_v],
                    mode="markers", name=f"{tech} VALCOE",
                    marker=dict(size=6, symbol="square",
                                color=[p["year"] for p in pts_v],
                                colorscale="RdYlBu_r", cmin=yr_min, cmax=yr_max),
                    showlegend=False,
                    hovertemplate="%{y:.1f} $/MWh<extra>VALCOE</extra>",
                ), row=2, col=1)

        fig.update_yaxes(title_text="Capacity Factor (%)", range=[0, 100], row=1, col=1)
        all_costs = [r["lcoe"] for r in records if r["lcoe"] > 0] + \
                    [r["vallcoe"] for r in records if r["vallcoe"] > 0]
        if all_costs:
            fig.update_yaxes(title_text="$/MWh", range=[max(all_costs) * 1.1, 0], row=2, col=1)
        fig.update_layout(height=850)
        return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 9 — Electricity Cost Analysis
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ElectricityCostChart(PlotlyChart):
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

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> go.Figure:
        fig = make_subplots(rows=2, cols=1, row_heights=[0.6, 0.4], vertical_spacing=0.12,
                            subplot_titles=["Daily Electricity Price Evolution",
                                            "Price Distribution Comparison"])

        with h5py.File(h5_path, "r") as h5f:
            bp = kw.get("base_prefix", "")
            tres = _get_temporal_res(h5f)
            spd = max(1, 24 // tres)
            days_per_year = 365
            yr_list = []
            year_price_data = {}
            re_prices, nonre_prices = [], []

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

                if "technology_selling_prices" in sc:
                    tsp = sc["technology_selling_prices"]
                    for tech_name in tsp:
                        tg = tsp[tech_name]
                        tech_type = tg.attrs.get("technology_type", "")
                        if isinstance(tech_type, bytes):
                            tech_type = tech_type.decode()
                        is_re = _is_renewable(tech_name) or tech_type == "Renewable"
                        if "prices_weights" in tg:
                            pw = tg["prices_weights"][:]
                            for row in pw:
                                price, weight = float(row[0]), float(row[1])
                                if price > 0 and weight > 0:
                                    n_samples = max(1, int(weight / 10))
                                    target = re_prices if is_re else nonre_prices
                                    target.extend([price] * n_samples)

        n_years = len(yr_list)
        if not yr_list:
            fig.add_annotation(text="No price data", xref="paper", yref="paper",
                               x=0.5, y=0.5, showarrow=False)
            return fig

        cost_matrix = np.zeros((days_per_year, n_years))
        annual_avg = []
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
                valid_all = prices[prices > 0]
                annual_avg.append(np.mean(valid_all) if len(valid_all) > 0 else np.nan)
            else:
                annual_avg.append(np.nan)

        if self._sigma > 0:
            from scipy.ndimage import gaussian_filter1d
            cost_matrix = gaussian_filter1d(cost_matrix, sigma=self._sigma * 2, axis=0)

        # Heatmap
        fig.add_trace(go.Heatmap(
            z=cost_matrix, x=[str(y) for y in yr_list],
            y=list(range(365)),
            colorscale="Turbo", colorbar=dict(title="$/MWh", len=0.4, y=0.75),
            hovertemplate="Year: %{x}<br>Day: %{y}<br>Price: %{z:.1f} $/MWh<extra></extra>",
        ), row=1, col=1)

        # Annual avg overlay
        valid_mask = ~np.isnan(np.array(annual_avg))
        if np.any(valid_mask):
            fig.add_trace(go.Scatter(
                x=[str(yr_list[i]) for i in range(n_years) if valid_mask[i]],
                y=[annual_avg[i] for i in range(n_years) if valid_mask[i]],
                mode="lines+markers", name="Annual Avg",
                line=dict(color="white", width=3),
                marker=dict(size=7, color="white", line=dict(color="black", width=1.5)),
                yaxis="y3",
            ), row=1, col=1)

        # Price distribution
        if re_prices:
            fig.add_trace(go.Histogram(
                x=re_prices, name="Renewable", opacity=0.5,
                marker_color="#2CA02C", nbinsx=100, histnorm="probability density",
            ), row=2, col=1)
        if nonre_prices:
            fig.add_trace(go.Histogram(
                x=nonre_prices, name="Non-Renewable", opacity=0.5,
                marker_color="#D62728", nbinsx=100, histnorm="probability density",
            ), row=2, col=1)

        fig.update_xaxes(title_text="Price ($/MWh)", row=2, col=1)
        fig.update_yaxes(title_text="Density", row=2, col=1)
        fig.update_layout(height=800, barmode="overlay")
        return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 10 — Inter-Node Flows
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class InterNodeFlowsChart(PlotlyChart):
    TITLE = "Inter-Node Flows"
    TR_KEY = "results_charts.inter_node_flows"

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> go.Figure:
        fig = go.Figure()
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
            fig.add_annotation(text="No power flow data", xref="paper", yref="paper",
                               x=0.5, y=0.5, showarrow=False)
            return fig

        colors = get_tab10()
        for node in range(num_nodes):
            label = node_names[node] if node < len(node_names) else f"Node {node}"
            color = colors[node % len(colors)]
            imp_vals = [imports_by_yr.get(y, {}).get(node, 0) for y in yr_list]
            exp_vals = [exports_by_yr.get(y, {}).get(node, 0) for y in yr_list]

            fig.add_trace(go.Bar(
                x=[str(y) for y in yr_list], y=imp_vals,
                name=f"{label} (import)", marker_color=color, opacity=0.85,
            ))
            fig.add_trace(go.Bar(
                x=[str(y) for y in yr_list], y=[-v for v in exp_vals],
                name=f"{label} (export)", marker_color=color, opacity=0.4,
                showlegend=False,
            ))

        fig.update_layout(
            title="Inter-Node Power Flows (Imports + / Exports -)",
            yaxis_title="Energy (GWh)",
            barmode="relative", height=500,
        )
        return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 11 — MGA Comparison
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MGAComparisonChart(PlotlyChart):
    TITLE = "MGA"
    TR_KEY = "results_charts.mga_comparison"

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> go.Figure:
        fig = make_subplots(
            rows=2, cols=2, vertical_spacing=0.15, horizontal_spacing=0.1,
            subplot_titles=["Investment Portfolio", "Cost vs Diversity",
                            "RE Penetration Trajectories", "Alternatives Summary"],
        )

        with h5py.File(h5_path, "r") as f:
            if "mga" not in f:
                fig.add_annotation(
                    text="No MGA results available.<br>"
                         "Enable MGA in settings and re-run.",
                    xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False,
                    font=dict(size=14),
                )
                return fig

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
            fig.add_annotation(text="No alternatives found", xref="paper", yref="paper",
                               x=0.5, y=0.5, showarrow=False)
            return fig

        colors = get_tab10()
        n_alts = len(alt_ids)

        # Investment portfolio
        inv_matrix = np.array([np.concatenate([alt_gen_inv[i], alt_bat_inv[i]]) for i in range(n_alts)])
        active = inv_matrix.sum(axis=0) > 0.01
        active_names = [tech_names[j] for j in range(len(tech_names)) if j < len(active) and active[j]]
        active_inv = inv_matrix[:, :len(active)][:, active]

        for i in range(n_alts):
            label = "Optimal" if alt_ids[i] == 0 else f"Alt {alt_ids[i]}"
            fig.add_trace(go.Bar(
                x=active_names, y=active_inv[i] if len(active_names) > 0 else [],
                name=label, marker_color=colors[i % len(colors)], opacity=0.85,
            ), row=1, col=1)

        # Cost vs Diversity
        for i in range(n_alts):
            marker_sym = "star" if alt_ids[i] == 0 else "circle"
            size = 15 if alt_ids[i] == 0 else 10
            fig.add_trace(go.Scatter(
                x=[alt_diversity[i]], y=[alt_costs[i] / 1e6],
                mode="markers", name=f"Alt {alt_ids[i]}" if alt_ids[i] > 0 else "Optimal",
                marker=dict(symbol=marker_sym, size=size, color=colors[i % len(colors)]),
                showlegend=False,
            ), row=1, col=2)
        if optimal_cost > 0 and slack > 0:
            fig.add_hline(y=optimal_cost * (1 + slack) / 1e6, line_dash="dash",
                          line_color="gray", row=1, col=2,
                          annotation_text=f"Cost limit (+{slack*100:.0f}%)")

        # RE penetration
        for i in range(n_alts):
            rp = alt_re_pen[i]
            yrs = mga_years[:len(rp)]
            dash = "solid" if alt_ids[i] == 0 else "dash"
            lw = 2.5 if alt_ids[i] == 0 else 1.5
            fig.add_trace(go.Scatter(
                x=yrs, y=rp * 100,
                mode="lines", name=f"Alt {alt_ids[i]}" if alt_ids[i] > 0 else "Optimal",
                line=dict(color=colors[i % len(colors)], width=lw, dash=dash),
                showlegend=False,
            ), row=2, col=1)

        # Summary table
        header_vals = ["Alt", "Cost ($M)", "+%", "Diversity"]
        cell_vals = [[], [], [], []]
        for i in range(n_alts):
            cell_vals[0].append(str(alt_ids[i]))
            cell_vals[1].append(f"${alt_costs[i]/1e6:,.1f}M")
            pct = (alt_costs[i] - optimal_cost) / optimal_cost * 100 if optimal_cost > 0 else 0
            cell_vals[2].append(f"+{pct:.1f}%" if pct > 0.01 else "0.0%")
            cell_vals[3].append(f"{alt_diversity[i]:.2f}")

        fig.add_trace(go.Table(
            header=dict(values=header_vals, fill_color="rgba(0,0,0,0.05)",
                        font=dict(size=11, color="black"), align="center"),
            cells=dict(values=cell_vals, align="center", font=dict(size=10)),
        ), row=2, col=2)

        fig.update_yaxes(title_text="Investment (MW)", row=1, col=1)
        fig.update_yaxes(title_text="Cost ($M)", row=1, col=2)
        fig.update_xaxes(title_text="Diversity", row=1, col=2)
        fig.update_yaxes(title_text="RE %", row=2, col=1)
        fig.update_layout(barmode="group", height=800,
                          legend=dict(orientation="h", y=-0.05, x=0.5, xanchor="center"))
        return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 12 — Fuel Supply
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FuelSupplyChart(PlotlyChart):
    TITLE = "Fuel Supply"
    TR_KEY = "results_charts.fuel_supply"

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> go.Figure:
        fig = make_subplots(rows=2, cols=1, vertical_spacing=0.12,
                            subplot_titles=["Fuel Supply by Year", "Demand Satisfied + Loss of Supply"])

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
            fig.add_annotation(text="No primary energy data", xref="paper", yref="paper",
                               x=0.5, y=0.5, showarrow=False)
            return fig

        sorted_years = sorted(year_supply.keys())
        fuel_list = sorted(all_fuels)
        fuel_colors = {f: _FUEL_COLORS[i % len(_FUEL_COLORS)] for i, f in enumerate(fuel_list)}

        for fuel in fuel_list:
            vals_s = [year_supply.get(y, {}).get(fuel, 0.0) for y in sorted_years]
            fig.add_trace(go.Bar(
                x=[str(y) for y in sorted_years], y=vals_s,
                name=fuel, marker_color=fuel_colors[fuel],
            ), row=1, col=1)

            vals_d = [year_demand.get(y, {}).get(fuel, 0.0) for y in sorted_years]
            fig.add_trace(go.Bar(
                x=[str(y) for y in sorted_years], y=vals_d,
                name=fuel, marker_color=fuel_colors[fuel], showlegend=False,
            ), row=2, col=1)

        total_loss_vals = [sum(year_loss.get(y, {}).values()) for y in sorted_years]
        if any(v > 0 for v in total_loss_vals):
            fig.add_trace(go.Bar(
                x=[str(y) for y in sorted_years], y=total_loss_vals,
                name="Loss of Supply", marker_color="#e74c3c", opacity=0.5,
                marker_pattern_shape="/",
            ), row=2, col=1)

        fig.update_layout(barmode="stack", height=700)
        fig.update_yaxes(title_text="Fuel Supply", row=1, col=1)
        fig.update_yaxes(title_text="Demand Satisfied", row=2, col=1)
        return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 13 — Fuel Costs
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FuelCostChart(PlotlyChart):
    TITLE = "Fuel Costs"
    TR_KEY = "results_charts.fuel_costs"

    def build_figure(self, h5_path: Path, years: list[int], **kw) -> go.Figure:
        fig = make_subplots(rows=2, cols=1, row_heights=[0.65, 0.35], vertical_spacing=0.12,
                            subplot_titles=["Cost Breakdown by Year", "Total PE Cost Evolution"])

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
            fig.add_annotation(text="No primary energy cost data", xref="paper", yref="paper",
                               x=0.5, y=0.5, showarrow=False)
            return fig

        sorted_years = sorted(cost_data.keys())
        x = [str(y) for y in sorted_years]

        for key, label, color in [
            ("total_fuel_cost", "Fuel Cost", "#3498db"),
            ("total_transport_cost", "Transport Cost", "#e67e22"),
            ("total_loss_penalty", "Loss Penalty", "#e74c3c"),
        ]:
            vals = [cost_data[y][key] / 1e3 for y in sorted_years]
            fig.add_trace(go.Bar(x=x, y=vals, name=label, marker_color=color), row=1, col=1)

        total_costs = [
            sum(cost_data[y].values()) / 1e3 for y in sorted_years
        ]
        fig.add_trace(go.Scatter(
            x=x, y=total_costs, name="Total PE Cost",
            mode="lines+markers", line=dict(color="#2c3e50", width=2),
            marker=dict(size=6),
        ), row=2, col=1)

        fig.update_layout(barmode="stack", height=650)
        fig.update_yaxes(title_text="Cost (k$)", row=1, col=1)
        fig.update_yaxes(title_text="Total PE Cost (k$)", row=2, col=1)
        fig.update_xaxes(title_text="Year", row=2, col=1)
        return fig


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Registry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PLOTLY_CHART_CLASSES = [
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
