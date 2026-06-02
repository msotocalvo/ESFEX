# -*- coding: utf-8 -*-
"""
Chart widgets for the Financial Analysis wizard.

All charts subclass ``FigureCanvasQTAgg`` and accept data from the
financial analysis engine (``esfex.models.financial_analysis``).
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure


# ──────────────────────────────────────────────────────────────
# Color palette (consistent across all financial charts)
# ──────────────────────────────────────────────────────────────

_COLORS = {
    "revenue": "#27ae60",
    "fuel": "#e74c3c",
    "om": "#e67e22",
    "capex": "#2c3e50",
    "tax": "#8e44ad",
    "insurance": "#f39c12",
    "salvage": "#1abc9c",
    "penalties": "#c0392b",
    "ptc": "#3498db",
    "debt_service": "#7f8c8d",
    "net_cf": "#2980b9",
    "equity_cf": "#16a085",
    "positive": "#27ae60",
    "negative": "#e74c3c",
    "neutral": "#7f8c8d",
    "highlight": "#f1c40f",
}

_TECH_PALETTE = [
    "#2980b9", "#e67e22", "#27ae60", "#e74c3c", "#8e44ad",
    "#1abc9c", "#f39c12", "#c0392b", "#3498db", "#16a085",
]


# ──────────────────────────────────────────────────────────────
# 1. NPV Waterfall Chart
# ──────────────────────────────────────────────────────────────


class WaterfallChart(FigureCanvasQTAgg):
    """NPV waterfall showing contribution of each cost/revenue component."""

    def __init__(self, figsize=(10, 5)):
        self.fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)

    def update_chart(
        self,
        labels: list[str],
        values: list[float],
        title: str = "NPV Waterfall ($)",
        last_is_total: bool = True,
    ):
        """Render a waterfall.

        When ``last_is_total`` (default), the final element is drawn as an
        absolute bar from 0 representing the cumulative total — it is NOT
        added on top of the running sum. The preceding elements are floating
        deltas; with a complete decomposition their running sum lands exactly
        on the total bar.
        """
        self.ax.clear()
        n = len(labels)
        if n == 0:
            self.draw()
            return

        colors = []
        bottoms = []
        heights = []
        tops = []  # y-level reached after each bar (for connectors/labels)
        running = 0.0
        for i, v in enumerate(values):
            if last_is_total and i == n - 1:
                # Absolute total bar from the zero baseline.
                bottoms.append(min(0.0, v))
                heights.append(abs(v))
                colors.append(_COLORS["net_cf"])
                tops.append(v)
            else:
                if v >= 0:
                    colors.append(_COLORS["positive"])
                    bottoms.append(running)
                    heights.append(v)
                else:
                    colors.append(_COLORS["negative"])
                    bottoms.append(running + v)
                    heights.append(abs(v))
                running += v
                tops.append(running)

        x = np.arange(n)
        self.ax.bar(x, heights, bottom=bottoms, color=colors, edgecolor="white", width=0.6)

        # Connector lines between consecutive bars
        for i in range(n - 1):
            self.ax.plot(
                [i + 0.3, i + 0.7], [tops[i]] * 2,
                color="#555", linewidth=0.8, linestyle="--",
            )

        # Value annotations
        for i, v in enumerate(values):
            is_total = last_is_total and i == n - 1
            y = tops[i]
            sign = "+" if (v >= 0 and not is_total) else ""
            self.ax.text(
                i, y, f"{sign}{v/1e6:.1f}M", ha="center",
                va="bottom" if v >= 0 else "top",
                fontsize=8, fontweight="bold",
            )

        self.ax.set_xticks(x)
        self.ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        self.ax.set_title(title, fontsize=11, fontweight="bold")
        self.ax.set_ylabel("$")
        self.ax.axhline(0, color="#555", linewidth=0.5)
        self.ax.grid(axis="y", alpha=0.3)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 2. Stacked Bar Chart (annual cost/revenue components)
# ──────────────────────────────────────────────────────────────


class StackedBarChart(FigureCanvasQTAgg):
    """Annual stacked bar chart showing cost and revenue components."""

    def __init__(self, figsize=(10, 5)):
        self.fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)

    def update_chart(self, cash_flows: pd.DataFrame, title: str = "Annual Cash Flows"):
        self.ax.clear()
        if cash_flows.empty:
            self.draw()
            return

        years = cash_flows["year"].values
        x = np.arange(len(years))

        # Positive (revenue / benefit) bars
        rev = cash_flows.get("revenue", np.zeros(len(years))).values
        ptc = cash_flows.get("ptc_benefit", np.zeros(len(years))).values
        itc = cash_flows.get("itc_benefit", np.zeros(len(years))).values
        pos_bottom = np.zeros(len(years))
        for vals, label, color in [
            (rev, "Revenue", _COLORS["revenue"]),
            (ptc, "PTC Benefit", _COLORS["ptc"]),
            (itc, "ITC Benefit", _COLORS["salvage"]),
        ]:
            self.ax.bar(
                x, vals, bottom=pos_bottom, label=label,
                color=color, width=0.4, align="edge",
            )
            pos_bottom += vals

        # Negative (cost) bars
        fuel = cash_flows.get("fuel_cost", np.zeros(len(years))).values
        om = cash_flows.get("om_cost", np.zeros(len(years))).values
        ins = cash_flows.get("insurance", np.zeros(len(years))).values
        carbon = cash_flows.get("carbon_cost", np.zeros(len(years))).values
        penalties = cash_flows.get("penalties", np.zeros(len(years))).values
        capex = cash_flows.get("capex", np.zeros(len(years))).values
        tax = cash_flows.get("tax", np.zeros(len(years))).values

        neg_bottom = np.zeros(len(years))
        for vals, label, color in [
            (fuel, "Fuel", _COLORS["fuel"]),
            (om, "O&M", _COLORS["om"]),
            (ins, "Insurance", _COLORS["insurance"]),
            (carbon, "Carbon", _COLORS["penalties"]),
            (penalties, "Penalties", _COLORS["negative"]),
            (capex, "CAPEX", _COLORS["capex"]),
            (tax, "Tax", _COLORS["tax"]),
        ]:
            self.ax.bar(
                x - 0.4, -vals, bottom=-neg_bottom, label=label,
                color=color, width=0.4, align="edge",
            )
            neg_bottom += vals

        # Net cash flow line
        net = cash_flows.get("net_cash_flow", np.zeros(len(years))).values
        self.ax.plot(x, net, "ko-", markersize=4, label="Net Cash Flow", linewidth=1.5)

        self.ax.set_xticks(x)
        self.ax.set_xticklabels(years, rotation=45, fontsize=8)
        self.ax.set_title(title, fontsize=11, fontweight="bold")
        self.ax.set_ylabel("$")
        self.ax.axhline(0, color="#555", linewidth=0.5)
        self.ax.legend(fontsize=7, ncol=4, loc="upper right")
        self.ax.grid(axis="y", alpha=0.3)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 3. Pie Chart (cost decomposition)
# ──────────────────────────────────────────────────────────────


class CostPieChart(FigureCanvasQTAgg):
    """Pie chart showing NPV cost decomposition."""

    def __init__(self, figsize=(6, 5)):
        self.fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)

    def update_chart(self, labels: list[str], values: list[float], title: str = "Cost Breakdown"):
        self.ax.clear()
        # Filter out zero/negative values
        filtered = [(l, v) for l, v in zip(labels, values) if v > 0]
        if not filtered:
            self.ax.text(0.5, 0.5, "No data", ha="center", va="center")
            self.draw()
            return
        labs, vals = zip(*filtered)
        colors = _TECH_PALETTE[:len(vals)]
        self.ax.pie(
            vals, labels=labs, autopct="%1.1f%%", colors=colors,
            startangle=90, textprops={"fontsize": 8},
        )
        self.ax.set_title(title, fontsize=11, fontweight="bold")
        self.draw()


# ──────────────────────────────────────────────────────────────
# 4. Tornado Diagram
# ──────────────────────────────────────────────────────────────


class TornadoDiagram(FigureCanvasQTAgg):
    """Two-sided horizontal bar chart showing sensitivity impacts."""

    def __init__(self, figsize=(10, 5)):
        self.fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)

    def update_chart(
        self,
        tornado: dict[str, tuple[float, float]],
        base_npv: float,
        title: str = "Tornado Diagram — NPV Sensitivity",
    ):
        self.ax.clear()
        if not tornado:
            self.draw()
            return

        # Sort by total swing (descending)
        items = sorted(
            tornado.items(),
            key=lambda kv: abs(kv[1][1] - kv[1][0]),
            reverse=True,
        )

        labels = [k for k, _ in items]
        lows = np.array([v[0] for _, v in items])
        highs = np.array([v[1] for _, v in items])

        y = np.arange(len(labels))
        left = np.minimum(lows, highs) - base_npv
        right = np.maximum(lows, highs) - base_npv
        widths = right - left

        self.ax.barh(
            y, widths, left=left + base_npv, height=0.5,
            color=[_COLORS["negative"] if l < h else _COLORS["positive"] for l, h in zip(lows, highs)],
            edgecolor="white",
        )
        self.ax.axvline(base_npv, color="#333", linewidth=1.2, linestyle="--", label=f"Base NPV: ${base_npv/1e6:.1f}M")

        self.ax.set_yticks(y)
        self.ax.set_yticklabels(labels, fontsize=9)
        self.ax.set_xlabel("NPV ($)")
        self.ax.set_title(title, fontsize=11, fontweight="bold")
        self.ax.legend(fontsize=8)
        self.ax.grid(axis="x", alpha=0.3)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 5. Spider Plot (multi-variable sensitivity)
# ──────────────────────────────────────────────────────────────


class SpiderPlot(FigureCanvasQTAgg):
    """Overlay line plot showing NPV vs % change for multiple variables."""

    def __init__(self, figsize=(8, 5)):
        self.fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)

    def update_chart(
        self,
        sweeps: dict[str, list[tuple]],
        base_npv: float,
        title: str = "Sensitivity Spider Plot",
    ):
        self.ax.clear()
        if not sweeps:
            self.draw()
            return

        for i, (var, points) in enumerate(sweeps.items()):
            vals = [p[0] for p in points]
            npvs = [p[1] for p in points]
            # Normalize x-axis as % change from base
            base_val = vals[len(vals) // 2]
            if base_val != 0:
                pct_change = [(v - base_val) / abs(base_val) * 100 for v in vals]
            else:
                pct_change = list(range(len(vals)))

            color = _TECH_PALETTE[i % len(_TECH_PALETTE)]
            self.ax.plot(pct_change, npvs, "o-", label=var, color=color, markersize=3)

        self.ax.axhline(base_npv, color="#555", linewidth=0.5, linestyle="--")
        self.ax.axvline(0, color="#555", linewidth=0.5, linestyle="--")
        self.ax.set_xlabel("% Change from Base", fontsize=9)
        self.ax.set_ylabel("NPV ($)", fontsize=9)
        self.ax.set_title(title, fontsize=11, fontweight="bold")
        self.ax.legend(fontsize=7, loc="best")
        self.ax.grid(alpha=0.3)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 6. Bubble Chart (technology comparison)
# ──────────────────────────────────────────────────────────────


class BubbleChart(FigureCanvasQTAgg):
    """Bubble chart: X=capacity factor, Y=LCOE, size=installed MW."""

    def __init__(self, figsize=(8, 5)):
        self.fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)

    def update_chart(
        self,
        tech_financials: dict,
        title: str = "Technology Comparison",
    ):
        """
        Parameters
        ----------
        tech_financials : dict[str, TechnologyFinancials]
        """
        self.ax.clear()
        if not tech_financials:
            self.draw()
            return

        names = []
        cfs = []
        lcoes = []
        sizes = []
        colors = []

        for i, (name, tf) in enumerate(tech_financials.items()):
            if tf.generation_mwh <= 0:
                continue
            names.append(name)
            cfs.append(tf.capacity_factor * 100)
            lcoes.append(min(tf.lcoe, 500))  # cap for display
            mw = tf.installed_mw if tf.installed_mw > 0 else 1
            sizes.append(max(mw * 5, 30))
            colors.append(_TECH_PALETTE[i % len(_TECH_PALETTE)])

        if not names:
            self.ax.text(0.5, 0.5, "No data", ha="center", va="center")
            self.draw()
            return

        self.ax.scatter(cfs, lcoes, s=sizes, c=colors, alpha=0.7, edgecolors="white")
        for i, name in enumerate(names):
            self.ax.annotate(name, (cfs[i], lcoes[i]), fontsize=7, ha="center", va="bottom")

        self.ax.set_xlabel("Capacity Factor (%)", fontsize=9)
        self.ax.set_ylabel("LCOE ($/MWh)", fontsize=9)
        self.ax.set_title(title, fontsize=11, fontweight="bold")
        self.ax.grid(alpha=0.3)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 7. DSCR Timeline
# ──────────────────────────────────────────────────────────────


class DSCRTimeline(FigureCanvasQTAgg):
    """Annual DSCR bar chart with 1.2× threshold line."""

    def __init__(self, figsize=(8, 4)):
        self.fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)

    def update_chart(self, years: list[int], dscr: np.ndarray, title: str = "Debt Service Coverage Ratio"):
        self.ax.clear()
        n = len(years)
        if n == 0:
            self.draw()
            return

        dscr_display = np.clip(dscr[:n], 0, 5)  # cap for display
        x = np.arange(n)
        colors = [_COLORS["positive"] if d >= 1.2 else _COLORS["negative"] for d in dscr_display]

        self.ax.bar(x, dscr_display, color=colors, edgecolor="white", width=0.6)
        self.ax.axhline(1.2, color="#e74c3c", linewidth=1.5, linestyle="--", label="Min DSCR (1.2×)")
        self.ax.axhline(1.0, color="#555", linewidth=0.5, linestyle=":")

        self.ax.set_xticks(x)
        self.ax.set_xticklabels(years, rotation=45, fontsize=8)
        self.ax.set_ylabel("DSCR")
        self.ax.set_title(title, fontsize=11, fontweight="bold")
        self.ax.legend(fontsize=8)
        self.ax.grid(axis="y", alpha=0.3)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 8. NPV Histogram (Monte Carlo)
# ──────────────────────────────────────────────────────────────


class NPVHistogram(FigureCanvasQTAgg):
    """Monte Carlo NPV distribution with VaR/CVaR annotations."""

    def __init__(self, figsize=(8, 5)):
        self.fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)

    def update_chart(
        self,
        npv_samples: np.ndarray,
        var_5: float = 0.0,
        cvar_5: float = 0.0,
        title: str = "NPV Distribution (Monte Carlo)",
    ):
        self.ax.clear()
        if len(npv_samples) == 0:
            self.draw()
            return

        self.ax.hist(
            npv_samples / 1e6, bins=min(50, len(npv_samples) // 2 + 1),
            color=_COLORS["net_cf"], alpha=0.7, edgecolor="white",
        )

        # VaR and CVaR lines
        self.ax.axvline(
            var_5 / 1e6, color=_COLORS["negative"], linewidth=1.5,
            linestyle="--", label=f"VaR 5%: ${var_5/1e6:.1f}M",
        )
        self.ax.axvline(
            cvar_5 / 1e6, color="#c0392b", linewidth=1.5,
            linestyle=":", label=f"CVaR 5%: ${cvar_5/1e6:.1f}M",
        )

        mean = float(np.mean(npv_samples))
        self.ax.axvline(
            mean / 1e6, color=_COLORS["positive"], linewidth=1.5,
            linestyle="-", label=f"Mean: ${mean/1e6:.1f}M",
        )

        self.ax.set_xlabel("NPV ($M)", fontsize=9)
        self.ax.set_ylabel("Frequency", fontsize=9)
        self.ax.set_title(title, fontsize=11, fontweight="bold")
        self.ax.legend(fontsize=8)
        self.ax.grid(alpha=0.3)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 9. Price Duration Curve
# ──────────────────────────────────────────────────────────────


class PriceDurationCurve(FigureCanvasQTAgg):
    """Sorted energy prices plot."""

    def __init__(self, figsize=(8, 4)):
        self.fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)

    def update_chart(self, prices: np.ndarray, title: str = "Price Duration Curve"):
        self.ax.clear()
        if len(prices) == 0:
            self.draw()
            return

        sorted_prices = np.sort(prices)[::-1]
        x = np.linspace(0, 100, len(sorted_prices))
        self.ax.fill_between(x, sorted_prices, alpha=0.3, color=_COLORS["revenue"])
        self.ax.plot(x, sorted_prices, color=_COLORS["revenue"], linewidth=1.5)
        self.ax.set_xlabel("% of Time", fontsize=9)
        self.ax.set_ylabel("Price ($/MWh)", fontsize=9)
        self.ax.set_title(title, fontsize=11, fontweight="bold")
        self.ax.grid(alpha=0.3)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 10. Revenue vs Cost Bar Chart (per-technology)
# ──────────────────────────────────────────────────────────────


class TechRevenueVsCostChart(FigureCanvasQTAgg):
    """Grouped bar chart: revenue vs total cost per technology."""

    def __init__(self, figsize=(8, 5)):
        self.fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)

    def update_chart(self, tech_financials: dict, title: str = "Revenue vs Cost by Technology"):
        self.ax.clear()
        if not tech_financials:
            self.draw()
            return

        names = []
        revenues = []
        costs = []
        for name, tf in tech_financials.items():
            if tf.generation_mwh <= 0 and tf.capex_total <= 0:
                continue
            names.append(name)
            revenues.append(tf.revenue_total / 1e6)
            costs.append((tf.fuel_cost_total + tf.om_cost_total + tf.capex_total) / 1e6)

        if not names:
            self.draw()
            return

        x = np.arange(len(names))
        w = 0.35
        self.ax.bar(x - w / 2, revenues, w, label="Revenue", color=_COLORS["revenue"])
        self.ax.bar(x + w / 2, costs, w, label="Total Cost", color=_COLORS["fuel"])
        self.ax.set_xticks(x)
        self.ax.set_xticklabels(names, rotation=30, ha="right", fontsize=8)
        self.ax.set_ylabel("$M")
        self.ax.set_title(title, fontsize=11, fontweight="bold")
        self.ax.legend(fontsize=8)
        self.ax.grid(axis="y", alpha=0.3)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 11. Cumulative NPV Curve
# ──────────────────────────────────────────────────────────────


class CumulativeNPVChart(FigureCanvasQTAgg):
    """Cumulative NPV over project lifetime."""

    def __init__(self, figsize=(8, 4)):
        self.fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)

    def update_chart(self, cash_flows: pd.DataFrame, title: str = "Cumulative NPV"):
        self.ax.clear()
        if cash_flows.empty or "cumulative_npv" not in cash_flows.columns:
            self.draw()
            return

        years = cash_flows["year"].values
        cum_npv = cash_flows["cumulative_npv"].values / 1e6

        colors = [_COLORS["positive"] if v >= 0 else _COLORS["negative"] for v in cum_npv]
        self.ax.fill_between(years, cum_npv, alpha=0.2, color=_COLORS["net_cf"])
        self.ax.plot(years, cum_npv, "o-", color=_COLORS["net_cf"], markersize=4, linewidth=1.5)
        self.ax.axhline(0, color="#555", linewidth=0.5)

        self.ax.set_xlabel("Year", fontsize=9)
        self.ax.set_ylabel("Cumulative NPV ($M)", fontsize=9)
        self.ax.set_title(title, fontsize=11, fontweight="bold")
        self.ax.grid(alpha=0.3)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 12. IRR Histogram (Monte Carlo)
# ──────────────────────────────────────────────────────────────


class IRRHistogram(FigureCanvasQTAgg):
    """Monte Carlo IRR distribution."""

    def __init__(self, figsize=(8, 5)):
        self.fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)

    def update_chart(
        self,
        irr_samples: np.ndarray,
        wacc: float = 0.0,
        title: str = "IRR Distribution (Monte Carlo)",
    ):
        self.ax.clear()
        if len(irr_samples) == 0:
            self.draw()
            return

        # Convert to percentage
        pct = irr_samples * 100
        self.ax.hist(
            pct, bins=min(40, len(pct) // 2 + 1),
            color=_COLORS["equity_cf"], alpha=0.7, edgecolor="white",
        )
        if wacc > 0:
            self.ax.axvline(
                wacc * 100, color=_COLORS["negative"], linewidth=1.5,
                linestyle="--", label=f"WACC: {wacc:.1%}",
            )
        mean_irr = float(np.mean(irr_samples))
        self.ax.axvline(
            mean_irr * 100, color=_COLORS["positive"], linewidth=1.5,
            label=f"Mean IRR: {mean_irr:.1%}",
        )
        self.ax.set_xlabel("IRR (%)", fontsize=9)
        self.ax.set_ylabel("Frequency", fontsize=9)
        self.ax.set_title(title, fontsize=11, fontweight="bold")
        self.ax.legend(fontsize=8)
        self.ax.grid(alpha=0.3)
        self.draw()
