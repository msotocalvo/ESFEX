# -*- coding: utf-8 -*-
"""Chart widgets for the Risk & Resilience workbench.

All charts subclass ``FigureCanvasQTAgg`` and visualise hazard exposure,
fragility curves, composite risk indices, scenario trees, sensitivity
analysis, and climate demand projections.
"""

from __future__ import annotations

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt

# ──────────────────────────────────────────────────────────────
# Colour palette
# ──────────────────────────────────────────────────────────────

_HAZARD_COLORS = {
    "earthquake": "#e74c3c",
    "cyclone": "#5DADE2",
    "flood": "#2471A3",
    "tsunami": "#1abc9c",
    "wildfire": "#e67e22",
    "volcanic": "#8e44ad",
    "sea_level_rise": "#16a085",
}

_RISK_PALETTE = [
    "#27ae60", "#f1c40f", "#e67e22", "#e74c3c", "#c0392b",
]

_SSP_COLORS = {
    "SSP1-2.6": "#2ecc71",
    "SSP2-4.5": "#f39c12",
    "SSP3-7.0": "#e74c3c",
    "SSP5-8.5": "#8e44ad",
}

_RP_COLORS = {
    "RP 50yr": "#3498db",
    "RP 100yr": "#2ecc71",
    "RP 250yr": "#f1c40f",
    "RP 475yr": "#e67e22",
    "RP 500yr": "#e74c3c",
    "RP 1000yr": "#c0392b",
    "RP 2500yr": "#8e44ad",
}

# ──────────────────────────────────────────────────────────────
# Semantic font sizes for consistent professional styling
# ──────────────────────────────────────────────────────────────

_FONT_TITLE = 13
_FONT_LABEL = 11
_FONT_TICK = 10
_FONT_LEGEND = 9
_FONT_ANNOTATION = 8

# ──────────────────────────────────────────────────────────────
# Base chart class with export and standardized styling
# ──────────────────────────────────────────────────────────────

_STANDARD_DPI = 100


def _export_dpi() -> int:
    """Return preferred export DPI from user preferences."""
    try:
        from esfex.visualization.preferences import get_export_dpi
        return get_export_dpi()
    except Exception:
        return 300


class _BaseRiskChart(FigureCanvasQTAgg):
    """Base class for all risk workbench charts.

    Provides:
    - Standardized figure creation with consistent DPI and layout
    - Right-click context menu to export as PNG/SVG/PDF
    """

    def __init__(self, figsize=(9, 5.5), polar=False):
        if polar:
            self.fig = Figure(figsize=figsize, dpi=_STANDARD_DPI)
        else:
            self.fig = Figure(figsize=figsize, dpi=_STANDARD_DPI, layout="constrained")
        super().__init__(self.fig)
        self._polar = polar
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_export_menu)

    def _show_export_menu(self, pos):
        """Right-click context menu for chart export."""
        from PySide6.QtWidgets import QMenu, QFileDialog
        menu = QMenu(self)
        act_png = menu.addAction("Export as PNG...")
        act_svg = menu.addAction("Export as SVG...")
        act_pdf = menu.addAction("Export as PDF...")
        action = menu.exec(self.mapToGlobal(pos))
        if action == act_png:
            self._export("PNG Files (*.png)")
        elif action == act_svg:
            self._export("SVG Files (*.svg)")
        elif action == act_pdf:
            self._export("PDF Files (*.pdf)")

    def _export(self, file_filter: str):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Chart", "", file_filter,
        )
        if path:
            self.fig.savefig(path, dpi=_export_dpi(), bbox_inches="tight")

    def _create_axes(self):
        """Create or recreate axes (handles polar case)."""
        self.fig.clear()
        if self._polar:
            return self.fig.add_subplot(111, polar=True)
        return self.fig.add_subplot(111)


# ──────────────────────────────────────────────────────────────
# 1. Fragility Curve Chart
# ──────────────────────────────────────────────────────────────


class FragilityCurveChart(_BaseRiskChart):
    """Plot fragility curves P(DS >= ds | IM) for selected component/hazard."""

    def __init__(self):
        super().__init__()
        self.ax = self.fig.add_subplot(111)

    def update_chart(
        self,
        curves: list[dict],
        title: str = "Fragility Curves",
        xlabel: str = "Intensity Measure",
    ):
        """Plot one or more fragility curves with optional epistemic bounds.

        curves: [{label, im_median, beta, beta_epistemic (opt),
                  source_quality (opt), color (opt)}, ...]
        """
        self.ax.clear()
        if not curves:
            self.draw()
            return

        from scipy.stats import norm

        im_range = np.linspace(0.01, max(c["im_median"] * 3 for c in curves), 200)

        for i, c in enumerate(curves):
            median = c["im_median"]
            beta = c["beta"]
            beta_e = c.get("beta_epistemic", 0.0)
            color = c.get("color", _RISK_PALETTE[i % len(_RISK_PALETTE)])
            ln_im = np.log(im_range) - np.log(median)

            probs = norm.cdf(ln_im / beta)
            self.ax.plot(im_range, probs, color=color, linewidth=2, label=c["label"])

            # Epistemic uncertainty bands (5th–95th percentile)
            if beta_e > 0:
                import math
                beta_total_hi = math.sqrt(beta**2 + beta_e**2)
                beta_total_lo = max(beta - beta_e * 0.5, 0.05)
                probs_hi = norm.cdf(ln_im / beta_total_lo)  # narrower β → steeper → higher P
                probs_lo = norm.cdf(ln_im / beta_total_hi)  # wider β → flatter → lower P
                self.ax.fill_between(
                    im_range, probs_lo, probs_hi,
                    color=color, alpha=0.12,
                )

        self.ax.set_xlabel(xlabel, fontsize=_FONT_LABEL)
        self.ax.set_ylabel(r"$P(DS \geq ds \mid IM)$", fontsize=_FONT_LABEL)
        self.ax.set_title(title, fontsize=_FONT_TITLE, fontweight="bold")
        self.ax.set_ylim(0, 1.05)
        self.ax.legend(fontsize=_FONT_LEGEND)
        self.ax.grid(True, alpha=0.3)
        self.ax.spines["top"].set_visible(False)
        self.ax.spines["right"].set_visible(False)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 2. Hazard Screening Heatmap (multi-hazard × nodes)
# ──────────────────────────────────────────────────────────────


class HazardScreeningChart(_BaseRiskChart):
    """Heatmap of hazard data (nodes × categories).

    Works for both integer screening levels (0-4) and continuous IM values.
    """

    def __init__(self):
        super().__init__()

    def update_chart(
        self,
        node_labels: list[str],
        hazard_levels: dict[str, list[float]],
        title: str = "Hazard Screening by Node",
        colors: dict[str, str] | None = None,
        colorbar_label: str = "",
        normalize_columns: bool = False,
    ):
        """Node × Category heatmap.

        Parameters
        ----------
        hazard_levels : {category: [value_per_node]}
        colorbar_label : label for colorbar; auto-detected if empty.
        normalize_columns : if True, each column is scaled to [0, 1]
            independently (useful when columns have different units).
            Cell annotations still show the original values.
        """
        self.fig.clear()
        if not node_labels or not hazard_levels:
            self.draw()
            return

        ax = self.fig.add_subplot(111)
        categories = list(hazard_levels.keys())
        raw = np.array([hazard_levels[cat] for cat in categories]).T  # (nodes, cats)

        if raw.size == 0:
            self.draw()
            return

        # Build display matrix (may be normalised)
        if normalize_columns:
            display = np.zeros_like(raw, dtype=float)
            for j in range(raw.shape[1]):
                col_max = float(np.nanmax(raw[:, j]))
                if col_max > 0:
                    display[:, j] = raw[:, j] / col_max
                # else stays 0
            vmin, vmax = 0.0, 1.0
            is_screening = False
        else:
            display = raw.astype(float)
            vmin = 0.0
            vmax = float(np.nanmax(display))
            if vmax <= 0:
                vmax = 1.0
            is_screening = vmax <= 4.0 and np.allclose(raw, raw.astype(int))
            if is_screening:
                vmax = 4.0

        im = ax.imshow(
            display, aspect="auto", cmap="YlOrRd",
            vmin=vmin, vmax=vmax, interpolation="nearest",
        )

        ax.set_xticks(range(len(categories)))
        ax.set_xticklabels(
            [c.replace("_", " ").title() for c in categories],
            rotation=45, ha="right", fontsize=_FONT_TICK,
        )
        ax.set_yticks(range(len(node_labels)))
        ax.set_yticklabels(node_labels, fontsize=_FONT_TICK)
        ax.set_title(title, fontsize=_FONT_TITLE, fontweight="bold")

        if not colorbar_label:
            if normalize_columns:
                colorbar_label = "Relative Intensity (per hazard)"
            elif is_screening:
                colorbar_label = "Hazard Level (0 = None → 4 = Very High)"
            else:
                colorbar_label = "Intensity Measure"
        self.fig.colorbar(im, ax=ax, shrink=0.8, label=colorbar_label)

        # Annotate — always show raw values
        fmt = "{:.0f}" if is_screening else "{:.2g}"
        for i in range(raw.shape[0]):
            for j in range(raw.shape[1]):
                disp_val = display[i, j]
                txt_color = "white" if disp_val > vmax * 0.55 else "black"
                ax.text(
                    j, i, fmt.format(raw[i, j]),
                    ha="center", va="center", fontsize=_FONT_TICK, color=txt_color,
                    fontweight="bold",
                )

        self.draw()


# ──────────────────────────────────────────────────────────────
# 3. Composite Risk Heatmap
# ──────────────────────────────────────────────────────────────


class RiskHeatmapChart(_BaseRiskChart):
    """Node × Hazard heatmap of failure probabilities."""

    def __init__(self):
        super().__init__()
        self.ax = self.fig.add_subplot(111)

    def update_chart(
        self,
        node_labels: list[str],
        hazard_types: list[str],
        matrix: np.ndarray,
        title: str = "Composite Risk Matrix",
    ):
        """matrix: shape (n_nodes, n_hazards), values 0-1."""
        self.ax.clear()
        if matrix.size == 0:
            self.draw()
            return

        im = self.ax.imshow(matrix, aspect="auto", cmap="YlOrRd", vmin=0, vmax=1)
        self.ax.set_xticks(range(len(hazard_types)))
        self.ax.set_xticklabels(
            [h.replace("_", " ").title() for h in hazard_types],
            rotation=45, ha="right", fontsize=_FONT_TICK,
        )
        self.ax.set_yticks(range(len(node_labels)))
        self.ax.set_yticklabels(node_labels, fontsize=_FONT_TICK)
        self.ax.set_title(title)
        self.fig.colorbar(im, ax=self.ax, label="Failure Probability", shrink=0.8)

        # Annotate cells
        for i in range(matrix.shape[0]):
            for j in range(matrix.shape[1]):
                val = matrix[i, j]
                color = "white" if val > 0.5 else "black"
                self.ax.text(
                    j, i, f"{val:.2f}", ha="center", va="center",
                    fontsize=_FONT_ANNOTATION, color=color,
                )

        self.draw()


# ──────────────────────────────────────────────────────────────
# 4. Expected Annual Loss (EAL) Bar Chart
# ──────────────────────────────────────────────────────────────


class EALBarChart(_BaseRiskChart):
    """Stacked bar chart of expected annual loss by hazard type per node."""

    def __init__(self):
        super().__init__()
        self.ax = self.fig.add_subplot(111)

    def update_chart(
        self,
        node_labels: list[str],
        eal_by_hazard: dict[str, list[float]],
        title: str = "Expected Annual Loss by Node",
    ):
        """eal_by_hazard: {hazard_type: [eal_per_node]}."""
        self.ax.clear()
        if not node_labels or not eal_by_hazard:
            self.draw()
            return

        x = np.arange(len(node_labels))
        bottom = np.zeros(len(node_labels))

        for haz, vals in eal_by_hazard.items():
            color = _HAZARD_COLORS.get(haz, "#7f8c8d")
            self.ax.bar(
                x, vals, bottom=bottom, color=color,
                label=haz.replace("_", " ").title(),
            )
            bottom += np.array(vals)

        self.ax.set_xticks(x)
        self.ax.set_xticklabels(node_labels, rotation=45, ha="right", fontsize=_FONT_TICK)
        self.ax.set_ylabel("Expected Annual Loss ($/year)")
        self.ax.set_title(title)
        self.ax.legend(fontsize=_FONT_ANNOTATION)
        self.ax.grid(True, axis="y", alpha=0.3)
        self.ax.spines["top"].set_visible(False)
        self.ax.spines["right"].set_visible(False)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 5. Climate Scenario Comparison
# ──────────────────────────────────────────────────────────────


class ClimateScenarioChart(_BaseRiskChart):
    """Line chart comparing climate deltas across SSP pathways."""

    def __init__(self):
        super().__init__(figsize=(9, 8))
        self.axes = self.fig.subplots(3, 1, sharex=True)

    def update_chart(
        self,
        scenarios: list[dict],
        title: str = "Climate Projections",
    ):
        """scenarios: [{name, temperature_delta: {year: dT}, ghi_delta_fraction, wind_speed_delta_fraction}]."""
        for ax in self.axes:
            ax.clear()

        if not scenarios:
            self.draw()
            return

        # Temperature
        for sc in scenarios:
            color = _SSP_COLORS.get(sc["name"], "#7f8c8d")
            td = sc.get("temperature_delta", {})
            if td:
                years = sorted(td.keys())
                self.axes[0].plot(
                    years, [td[y] for y in years],
                    "o-", color=color, label=sc["name"],
                    markersize=6, linewidth=2,
                )
        self.axes[0].set_ylabel("\u0394T (\u00b0C)", fontsize=_FONT_TICK)
        self.axes[0].set_title("Temperature Change", fontsize=_FONT_LABEL, fontweight="bold")
        self.axes[0].legend(fontsize=_FONT_LEGEND, loc="upper left")
        self.axes[0].grid(True, alpha=0.3)

        # GHI
        for sc in scenarios:
            color = _SSP_COLORS.get(sc["name"], "#7f8c8d")
            ghi = sc.get("ghi_delta_fraction", {})
            if ghi:
                years = sorted(ghi.keys())
                self.axes[1].plot(
                    years, [ghi[y] * 100 for y in years],
                    "o-", color=color, markersize=6, linewidth=2,
                )
        self.axes[1].set_ylabel("GHI Change (%)", fontsize=_FONT_TICK)
        self.axes[1].set_title("Solar Irradiance", fontsize=_FONT_LABEL, fontweight="bold")
        self.axes[1].grid(True, alpha=0.3)

        # Wind
        for sc in scenarios:
            color = _SSP_COLORS.get(sc["name"], "#7f8c8d")
            ws = sc.get("wind_speed_delta_fraction", {})
            if ws:
                years = sorted(ws.keys())
                self.axes[2].plot(
                    years, [ws[y] * 100 for y in years],
                    "o-", color=color, markersize=6, linewidth=2,
                )
        self.axes[2].set_ylabel("Wind Change (%)", fontsize=_FONT_TICK)
        self.axes[2].set_title("Wind Speed", fontsize=_FONT_LABEL, fontweight="bold")
        self.axes[2].set_xlabel("Year", fontsize=_FONT_TICK)
        self.axes[2].grid(True, alpha=0.3)

        self.fig.suptitle(title, fontsize=_FONT_TITLE, fontweight="bold")
        self.draw()


# ──────────────────────────────────────────────────────────────
# 6. Scenario Tree Diagram
# ──────────────────────────────────────────────────────────────


class ScenarioTreeChart(_BaseRiskChart):
    """Simple scenario tree (probability-weighted branches)."""

    def __init__(self):
        super().__init__()
        self.ax = self.fig.add_subplot(111)

    def update_chart(
        self,
        climate_scenarios: list[dict],
        hazard_scenarios: list[dict],
        title: str = "Scenario Tree",
    ):
        """Draw a two-level tree: climate branches → hazard sub-branches."""
        self.ax.clear()
        self.ax.set_xlim(-0.5, 2.5)

        n_climate = max(len(climate_scenarios), 1)
        n_hazard = max(len(hazard_scenarios), 1)
        total = n_climate * n_hazard

        self.ax.set_ylim(-0.5, total + 0.5)

        # Root
        root_y = total / 2
        self.ax.plot(0, root_y, "ko", markersize=10)
        self.ax.annotate("Root", (0, root_y), textcoords="offset points",
                         xytext=(-30, 5), fontsize=_FONT_ANNOTATION)

        y_idx = 0
        for i, csc in enumerate(climate_scenarios):
            c_y_start = y_idx
            c_y_end = y_idx + n_hazard - 1
            c_y = (c_y_start + c_y_end) / 2

            # Climate branch
            color = _SSP_COLORS.get(csc.get("name", ""), "#7f8c8d")
            self.ax.plot([0, 1], [root_y, c_y], "-", color=color, linewidth=1.5)
            self.ax.plot(1, c_y, "o", color=color, markersize=8)
            prob_c = csc.get("probability", 0)
            self.ax.annotate(
                f'{csc.get("name", f"C{i}")}\np={prob_c:.2f}',
                (1, c_y), textcoords="offset points",
                xytext=(5, 5), fontsize=_FONT_ANNOTATION, color=color,
            )

            # Hazard sub-branches
            for j, hsc in enumerate(hazard_scenarios):
                h_y = y_idx + j
                self.ax.plot([1, 2], [c_y, h_y], "-", color="#7f8c8d", linewidth=0.8)
                self.ax.plot(2, h_y, "s", color="#e74c3c", markersize=5)
                prob_h = hsc.get("probability", 0)
                self.ax.annotate(
                    f'{hsc.get("name", f"H{j}")} (p={prob_h:.3f})',
                    (2, h_y), textcoords="offset points",
                    xytext=(5, 0), fontsize=_FONT_ANNOTATION,
                )

            y_idx += n_hazard

        self.ax.set_title(title)
        self.ax.axis("off")
        self.draw()


# ──────────────────────────────────────────────────────────────
# 7. IM Exceedance Chart
# ──────────────────────────────────────────────────────────────


class IMExceedanceChart(_BaseRiskChart):
    """Single-axes exceedance curve plot (return period vs IM) for one hazard."""

    _NODE_COLORS = [
        "#2c3e50", "#e74c3c", "#3498db", "#27ae60", "#f39c12",
        "#8e44ad", "#1abc9c", "#e67e22",
    ]

    def __init__(self):
        super().__init__()

    def update_chart(
        self,
        node_curves: dict[str, dict[int, float]],
        hazard_type: str = "",
        units: str = "",
    ):
        """Plot exceedance curves for a single hazard type.

        node_curves: {node_label: {return_period: im_value}}
        """
        self.fig.clear()
        if not node_curves:
            self.draw()
            return

        ax = self.fig.add_subplot(111)
        for i, (label, rp_im) in enumerate(node_curves.items()):
            if not rp_im:
                continue
            rps = sorted(rp_im.keys())
            ims = [rp_im[rp] for rp in rps]
            color = self._NODE_COLORS[i % len(self._NODE_COLORS)]
            ax.plot(rps, ims, "o-", color=color, linewidth=2,
                    markersize=5, label=label)

        ax.set_xscale("log")
        ax.set_xlabel("Return Period (years)", fontsize=_FONT_LABEL)
        ylabel = f"Intensity Measure ({units})" if units else "Intensity Measure"
        ax.set_ylabel(ylabel, fontsize=_FONT_LABEL)
        title = hazard_type.replace("_", " ").title() if hazard_type else "Hazard Exceedance"
        haz_key = hazard_type.lower().replace(" ", "_")
        accent = _HAZARD_COLORS.get(haz_key, "#2c3e50")
        ax.set_title(title, fontsize=_FONT_TITLE, fontweight="bold", color=accent)
        ax.legend(fontsize=_FONT_LEGEND)
        ax.grid(True, alpha=0.3)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 8. Sensitivity Tornado Chart
# ──────────────────────────────────────────────────────────────


class SensitivityTornadoChart(_BaseRiskChart):
    """Tornado diagram for risk parameter sensitivity analysis.

    Shows the impact of varying individual parameters (CVaR α, λ,
    combination method) on total EAL or composite risk.
    """

    def __init__(self):
        super().__init__()
        self.ax = self.fig.add_subplot(111)

    def update_chart(
        self,
        param_names: list[str],
        low_values: list[float],
        high_values: list[float],
        base_value: float,
        metric_label: str = "Total EAL ($/yr)",
    ):
        """Draw tornado bars centred on base_value.

        param_names: Parameter labels (e.g. ["CVaR α", "CVaR λ", "Method"])
        low_values:  Metric value when parameter is at low end
        high_values: Metric value when parameter is at high end
        base_value:  Metric value at baseline parameters
        """
        self.ax.clear()
        if not param_names:
            self.draw()
            return

        n = len(param_names)
        # Sort by impact (largest swing at top)
        swings = [abs(high_values[i] - low_values[i]) for i in range(n)]
        order = sorted(range(n), key=lambda k: swings[k])

        y = np.arange(len(order))
        sorted_names = [param_names[i] for i in order]
        sorted_low = [low_values[i] for i in order]
        sorted_high = [high_values[i] for i in order]

        for j, idx in enumerate(range(len(order))):
            lo = sorted_low[idx] - base_value
            hi = sorted_high[idx] - base_value
            self.ax.barh(j, lo, height=0.6, color="#3498db", alpha=0.8,
                         left=base_value)
            self.ax.barh(j, hi, height=0.6, color="#e74c3c", alpha=0.8,
                         left=base_value)

        self.ax.axvline(base_value, color="black", linewidth=1, linestyle="--",
                        label=f"Base: {base_value:,.0f}")
        self.ax.set_yticks(y)
        self.ax.set_yticklabels(sorted_names, fontsize=_FONT_TICK)
        self.ax.set_xlabel(metric_label, fontsize=_FONT_LABEL)
        self.ax.set_title("Risk Sensitivity Analysis", fontsize=_FONT_TITLE, fontweight="bold")
        self.ax.legend(fontsize=_FONT_LEGEND, loc="lower right")
        self.ax.grid(True, axis="x", alpha=0.3)
        self.ax.spines["top"].set_visible(False)
        self.ax.spines["right"].set_visible(False)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 9. Resilience Performance Curve (ISO 22372)
# ──────────────────────────────────────────────────────────────


class ResiliencePerformanceChart(_BaseRiskChart):
    """Performance curve showing system degradation and recovery (RISK-24)."""

    def __init__(self):
        super().__init__()
        self.ax = self.fig.add_subplot(111)

    def update_chart(
        self,
        time_steps: np.ndarray,
        performance_curve: np.ndarray,
        resilience_index: float,
    ):
        """Plot F_actual(t)/F_ideal with shaded area = lost performance."""
        self.ax.clear()
        if time_steps is None or len(time_steps) == 0:
            self.draw()
            return

        self.ax.fill_between(
            time_steps, performance_curve, 1.0,
            alpha=0.3, color="#e74c3c", label="Lost performance",
        )
        self.ax.fill_between(
            time_steps, 0, performance_curve,
            alpha=0.15, color="#27ae60",
        )
        self.ax.plot(
            time_steps, performance_curve, color="#2c3e50",
            linewidth=2, label="System performance",
        )
        self.ax.axhline(1.0, color="#7f8c8d", linestyle="--", linewidth=0.8, label="Ideal")
        self.ax.set_xlabel("Time (hours)")
        self.ax.set_ylabel("F(t) / F_ideal")
        self.ax.set_ylim(0, 1.1)
        self.ax.set_title(f"Resilience Performance Curve (R = {resilience_index:.3f})")
        self.ax.legend(fontsize=_FONT_LEGEND, loc="lower right")
        self.ax.grid(True, alpha=0.3)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 10. Resilience Radar Chart (ISO 22372 Four Capacities)
# ──────────────────────────────────────────────────────────────


class ResilienceRadarChart(_BaseRiskChart):
    """Radar/spider chart for resilience capacities."""

    def __init__(self):
        super().__init__(figsize=(6, 6), polar=True)
        self.ax = self.fig.add_subplot(111, polar=True)

    def update_chart(
        self,
        capacities: dict[str, float],
        title: str = "ISO 22372 Resilience Capacities",
    ):
        """Draw radar chart with capacity dimensions."""
        self.ax = self._create_axes()
        if not capacities:
            self.draw()
            return

        labels = list(capacities.keys())
        values = list(capacities.values())
        n = len(labels)

        angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
        values_closed = values + [values[0]]
        angles_closed = angles + [angles[0]]

        self.ax.plot(
            angles_closed, values_closed, "o-",
            linewidth=2, color="#2980b9", markersize=6,
        )
        self.ax.fill(angles_closed, values_closed, alpha=0.2, color="#2980b9")

        self.ax.set_xticks(angles)
        self.ax.set_xticklabels(labels, fontsize=_FONT_LEGEND)
        self.ax.set_ylim(0, 1.0)
        self.ax.set_yticks([0.25, 0.5, 0.75, 1.0])
        self.ax.set_yticklabels(["0.25", "0.50", "0.75", "1.00"], fontsize=_FONT_ANNOTATION)
        self.ax.set_title(title, fontsize=_FONT_LABEL, pad=20)
        self.ax.grid(True, alpha=0.3)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 11. Demand Adjustment Chart
# ──────────────────────────────────────────────────────────────


class DemandAdjustmentChart(_BaseRiskChart):
    """Preview of HDD/CDD demand modification across SSP pathways."""

    def __init__(self):
        super().__init__(figsize=(9, 7))

    def update_chart(
        self,
        ssp_demands: dict[str, dict[int, float]],
        base_demand: float = 1.0,
        monthly_breakdown: dict[str, list[float]] | None = None,
    ):
        """Plot demand multiplier vs year for each SSP.

        ssp_demands: {ssp_name: {year: demand_multiplier}}
        monthly_breakdown: optional {ssp_name: [12 monthly multipliers]}
        """
        self.fig.clear()
        if not ssp_demands:
            self.draw()
            return

        n_axes = 2 if monthly_breakdown else 1
        axes = self.fig.subplots(n_axes, 1)
        if n_axes == 1:
            axes = [axes]

        ax = axes[0]
        ax.axhline(1.0, color="black", linewidth=1, linestyle="--",
                    alpha=0.5, label="Baseline")

        for ssp, year_mult in ssp_demands.items():
            color = _SSP_COLORS.get(ssp, "#7f8c8d")
            years = sorted(year_mult.keys())
            mults = [year_mult[y] for y in years]
            ax.plot(years, mults, "o-", color=color, linewidth=2,
                     markersize=5, label=ssp)
            ax.fill_between(years, 1.0, mults, color=color, alpha=0.1)

        ax.set_xlabel("Year", fontsize=_FONT_LABEL)
        ax.set_ylabel("Demand Multiplier", fontsize=_FONT_LABEL)
        ax.set_title("Climate-Adjusted Demand Projection",
                      fontsize=_FONT_TITLE, fontweight="bold")
        ax.legend(fontsize=_FONT_LEGEND)
        ax.grid(True, alpha=0.3)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        if monthly_breakdown and n_axes > 1:
            ax2 = axes[1]
            months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            x = np.arange(12)
            width = 0.8 / max(len(monthly_breakdown), 1)
            for i, (ssp, vals) in enumerate(monthly_breakdown.items()):
                color = _SSP_COLORS.get(ssp, "#7f8c8d")
                offset = (i - len(monthly_breakdown) / 2 + 0.5) * width
                ax2.bar(x + offset, vals, width, color=color, alpha=0.7, label=ssp)
            ax2.set_xticks(x)
            ax2.set_xticklabels(months, fontsize=_FONT_TICK)
            ax2.set_ylabel("Demand Multiplier", fontsize=_FONT_LABEL)
            ax2.set_title("Monthly Demand Breakdown",
                          fontsize=_FONT_TITLE, fontweight="bold")
            ax2.axhline(1.0, color="black", linewidth=0.8, linestyle="--", alpha=0.5)
            ax2.legend(fontsize=_FONT_LEGEND)
            ax2.grid(True, axis="y", alpha=0.3)
            ax2.spines["top"].set_visible(False)
            ax2.spines["right"].set_visible(False)

        self.draw()


# ──────────────────────────────────────────────────────────────
# 10. Probability Pie Chart
# ──────────────────────────────────────────────────────────────


class ProbabilityPieChart(_BaseRiskChart):
    """Pie chart of scenario probability distribution."""

    def __init__(self):
        super().__init__()
        self.ax = self.fig.add_subplot(111)

    def update_chart(
        self,
        scenario_names: list[str],
        probabilities: list[float],
        title: str = "Scenario Probabilities",
    ):
        """Draw probability pie chart.

        scenario_names: Labels for each scenario.
        probabilities: Probability weights (should sum to ~1).
        """
        self.ax.clear()
        if not scenario_names or not probabilities:
            self.draw()
            return

        # Use distinct colours
        colors = []
        for name in scenario_names:
            if name in _SSP_COLORS:
                colors.append(_SSP_COLORS[name])
            elif any(h in name.lower() for h in _HAZARD_COLORS):
                for h, c in _HAZARD_COLORS.items():
                    if h in name.lower():
                        colors.append(c)
                        break
            else:
                idx = len(colors)
                fallback = ["#3498db", "#e74c3c", "#27ae60", "#f39c12",
                            "#8e44ad", "#1abc9c", "#e67e22", "#2c3e50"]
                colors.append(fallback[idx % len(fallback)])

        # Only show labels for slices > 3%
        labels = [
            f"{n}\n({p:.1%})" if p > 0.03 else ""
            for n, p in zip(scenario_names, probabilities)
        ]

        wedges, texts = self.ax.pie(
            probabilities, labels=labels, colors=colors,
            startangle=90, textprops={"fontsize": _FONT_ANNOTATION},
        )

        self.ax.set_title(title)
        self.draw()


# ──────────────────────────────────────────────────────────────
# 13. Risk Coefficient Bar Chart (per-element)
# ──────────────────────────────────────────────────────────────


class RiskCoefficientChart(_BaseRiskChart):
    """Bar chart of per-element risk coefficients, grouped by node."""

    def __init__(self):
        super().__init__()

    def update_chart(
        self,
        coefficients: dict[str, float],
        dominant_hazards: dict[str, str] | None = None,
        title: str = "Per-Element Risk Coefficients",
    ):
        """Draw risk coefficient bars for each generator/battery.

        coefficients: {element_key: risk_coefficient}
        dominant_hazards: {element_key: hazard_type} for coloring
        """
        self.fig.clear()
        if not coefficients:
            self.draw()
            return

        ax = self.fig.add_subplot(111)
        keys = list(coefficients.keys())
        vals = [coefficients[k] for k in keys]
        colors = []
        for k in keys:
            haz = (dominant_hazards or {}).get(k, "")
            colors.append(_HAZARD_COLORS.get(haz, "#3498db"))

        x = np.arange(len(keys))
        bars = ax.bar(x, vals, color=colors, alpha=0.85, edgecolor="white", linewidth=0.5)

        # Reference line at 1.0 (no risk)
        ax.axhline(1.0, color="#2c3e50", linewidth=1, linestyle="--", alpha=0.5,
                    label="No risk derating")

        # Highlight elements below 0.9
        for i, v in enumerate(vals):
            if v < 0.9:
                ax.annotate(
                    f"{v:.2f}", (x[i], v), textcoords="offset points",
                    xytext=(0, -12), ha="center", fontsize=_FONT_ANNOTATION,
                    fontweight="bold", color="#c0392b",
                )

        ax.set_xticks(x)
        ax.set_xticklabels(
            [k.replace("_", " ") for k in keys],
            rotation=45, ha="right", fontsize=_FONT_TICK,
        )
        ax.set_ylabel("Risk Coefficient", fontsize=_FONT_LABEL)
        ax.set_title(title, fontsize=_FONT_TITLE, fontweight="bold")
        ax.set_ylim(0, 1.1)
        ax.legend(fontsize=_FONT_LEGEND, loc="lower right")
        ax.grid(True, axis="y", alpha=0.3)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        self.draw()
