"""Reusable demand visualizer — interactive Plotly charts for per-node demand.

Used both by the Grid Builder (to inspect the forecast it produced per node)
and by the node properties panel (to view a single node's demand). Operates on
the in-memory hourly series carried by ``GuiNodeDemand`` (``.data``); no solver
results or files required.

The x-axis uses real datetimes (anchored at a configurable start year), so
Plotly auto-formats the ticks by zoom level — years across a multi-year series,
months within a year, days/hours when zoomed in. A red dashed line marks mean
demand, and a right-hand panel reports deep statistics for dissecting the
load behaviour.
"""

from __future__ import annotations

import calendar
import logging
from typing import Optional, Sequence

import numpy as np
import plotly.graph_objects as go
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    _WEBENGINE = True
except Exception:  # pragma: no cover - headless/test environments
    QWebEngineView = None  # type: ignore[assignment, misc]
    _WEBENGINE = False

from esfex.visualization.i18n import tr
from esfex.visualization.theme import current_theme

logger = logging.getLogger(__name__)

# Chart keys (stable identifiers; labels resolved via tr()).
CHART_HOURLY = "hourly"
CHART_DURATION = "duration"
CHART_HEATMAP = "heatmap"
_CHART_ORDER = (CHART_HOURLY, CHART_DURATION, CHART_HEATMAP)

# Sentinel combo entry that aggregates every node into the system total.
_SYSTEM_KEY = "__system__"

_MEAN_COLOR = "#e74c3c"  # red dashed mean line
_DEFAULT_START_YEAR = 2025


def _plotly_layout() -> dict:
    """Layout kwargs matching the active ESFEX theme."""
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
        margin=dict(l=70, r=30, t=60, b=50),
        hovermode="x unified",
    )


def _accent() -> str:
    return current_theme().colors.accent_primary


def _series_of(demand) -> np.ndarray:
    """Return a node's hourly series as a float array (empty if none)."""
    data = getattr(demand, "data", None)
    if not data:
        return np.asarray([], dtype=float)
    return np.asarray(data, dtype=float)


def _date_index(n: int, start_year: int):
    """Hourly pandas DatetimeIndex of length ``n`` anchored at ``start_year``."""
    import pandas as pd

    return pd.date_range(f"{start_year}-01-01", periods=n, freq="h")


def demand_stats(series: np.ndarray) -> dict:
    """Peak, mean, base, total energy and load factor for an hourly series."""
    if series.size == 0:
        return dict(hours=0, peak=0.0, mean=0.0, base=0.0,
                    total_gwh=0.0, load_factor=0.0)
    peak = float(series.max())
    mean = float(series.mean())
    return dict(
        hours=int(series.size),
        peak=peak,
        mean=mean,
        base=float(series.min()),
        total_gwh=float(series.sum()) / 1000.0,  # MWh -> GWh
        load_factor=(mean / peak if peak > 0 else 0.0),
    )


def deep_stats(series: np.ndarray, dates) -> list[tuple[str, list[tuple[str, str]]]]:
    """Grouped statistics for dissecting demand behaviour.

    Returns a list of ``(group_title, [(label, value), ...])`` ready to render.
    ``dates`` is the hourly DatetimeIndex aligned with ``series``.
    """
    import pandas as pd

    if series.size == 0:
        return []
    s = pd.Series(series, index=dates)
    peak = float(series.max())
    mean = float(series.mean())
    base = float(series.min())
    std = float(series.std())
    cv = std / mean if mean else 0.0

    def p(q):
        return float(np.percentile(series, q))

    diffs = np.diff(series)
    ramp_up = float(diffs.max()) if diffs.size else 0.0
    ramp_dn = float(diffs.min()) if diffs.size else 0.0

    by_hod = s.groupby(s.index.hour).mean()
    by_month = s.groupby(s.index.month).mean()
    weekday = float(s[s.index.dayofweek < 5].mean())
    weekend_mask = s.index.dayofweek >= 5
    weekend = float(s[weekend_mask].mean()) if weekend_mask.any() else 0.0
    peak_ts = s.idxmax()

    def mw(v):
        return f"{v:,.1f} MW"

    overview = [
        (tr("demand_visualizer.stat_hours"), f"{series.size:,}"),
        (tr("demand_visualizer.stat_peak"), mw(peak)),
        (tr("demand_visualizer.stat_mean"), mw(mean)),
        (tr("demand_visualizer.stat_median"), mw(p(50))),
        (tr("demand_visualizer.stat_base"), mw(base)),
        (tr("demand_visualizer.stat_std"), mw(std)),
        (tr("demand_visualizer.stat_cv"), f"{cv:.2f}"),
    ]
    distribution = [
        (tr("demand_visualizer.stat_p5"), mw(p(5))),
        (tr("demand_visualizer.stat_p25"), mw(p(25))),
        (tr("demand_visualizer.stat_p75"), mw(p(75))),
        (tr("demand_visualizer.stat_p95"), mw(p(95))),
        (tr("demand_visualizer.stat_p99"), mw(p(99))),
    ]
    energy = [
        (tr("demand_visualizer.stat_total"),
         f"{series.sum() / 1000.0:,.2f} GWh"),
        (tr("demand_visualizer.stat_daily_avg"),
         f"{series.sum() / max(series.size / 24.0, 1):,.1f} MWh"),
        (tr("demand_visualizer.stat_lf"),
         f"{mean / peak:.1%}" if peak else "—"),
        (tr("demand_visualizer.stat_peak_base"),
         f"{peak / base:.2f}" if base else "—"),
        (tr("demand_visualizer.stat_peak_mean"),
         f"{peak / mean:.2f}" if mean else "—"),
    ]
    temporal = [
        (tr("demand_visualizer.stat_peak_time"),
         peak_ts.strftime("%Y-%m-%d %H:%M")),
        (tr("demand_visualizer.stat_peak_hour"),
         f"{int(by_hod.idxmax()):02d}:00"),
        (tr("demand_visualizer.stat_low_hour"),
         f"{int(by_hod.idxmin()):02d}:00"),
        (tr("demand_visualizer.stat_peak_month"),
         calendar.month_abbr[int(by_month.idxmax())]),
        (tr("demand_visualizer.stat_low_month"),
         calendar.month_abbr[int(by_month.idxmin())]),
        (tr("demand_visualizer.stat_weekday"), mw(weekday)),
        (tr("demand_visualizer.stat_weekend"), mw(weekend)),
        (tr("demand_visualizer.stat_we_wd"),
         f"{(weekend / weekday - 1) * 100:+.1f}%" if weekday else "—"),
    ]
    variability = [
        (tr("demand_visualizer.stat_ramp_up"), f"{ramp_up:,.1f} MW/h"),
        (tr("demand_visualizer.stat_ramp_down"), f"{ramp_dn:,.1f} MW/h"),
        (tr("demand_visualizer.stat_ramp_mean"),
         f"{np.abs(diffs).mean():,.1f} MW/h" if diffs.size else "—"),
        (tr("demand_visualizer.stat_ramp_std"),
         f"{diffs.std():,.1f} MW/h" if diffs.size else "—"),
    ]
    return [
        (tr("demand_visualizer.group_overview"), overview),
        (tr("demand_visualizer.group_distribution"), distribution),
        (tr("demand_visualizer.group_energy"), energy),
        (tr("demand_visualizer.group_temporal"), temporal),
        (tr("demand_visualizer.group_variability"), variability),
    ]


class DemandVisualizerWidget(QWidget):
    """Plotly demand viewer: selectors + web chart + deep-statistics panel."""

    def __init__(self, parent=None, start_year: int = _DEFAULT_START_YEAR):
        super().__init__(parent)
        # list of (key, label, series) — key is node id/name or _SYSTEM_KEY
        # entry = (key, label, forecast_series, observed_series_or_None)
        self._entries: list[tuple] = []
        self._tmp_path: Optional[str] = None
        self._start_year = int(start_year) if start_year else _DEFAULT_START_YEAR

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        controls = QHBoxLayout()
        controls.addWidget(QLabel(tr("demand_visualizer.node")))
        self._node_combo = QComboBox()
        self._node_combo.currentIndexChanged.connect(self._on_node_changed)
        controls.addWidget(self._node_combo, 2)

        controls.addWidget(QLabel(tr("demand_visualizer.chart")))
        self._chart_combo = QComboBox()
        for key in _CHART_ORDER:
            self._chart_combo.addItem(tr(f"demand_visualizer.chart_{key}"), key)
        self._chart_combo.currentIndexChanged.connect(self._refresh)
        controls.addWidget(self._chart_combo, 2)
        controls.addStretch()
        outer.addLayout(controls)

        split = QSplitter(Qt.Orientation.Horizontal)

        if _WEBENGINE:
            self._view: QWidget = QWebEngineView(self)
        else:  # pragma: no cover
            self._view = QLabel(tr("demand_visualizer.no_webengine"))
            self._view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        split.addWidget(self._view)

        self._stats_scroll = QScrollArea()
        self._stats_scroll.setWidgetResizable(True)
        self._stats_scroll.setMinimumWidth(260)
        self._stats_scroll.setMaximumWidth(420)
        split.addWidget(self._stats_scroll)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        outer.addWidget(split, 1)

    # ── Qt lifecycle ──────────────────────────────────────────────
    def closeEvent(self, event):  # noqa: N802 - Qt override
        self._cleanup_tmp()
        super().closeEvent(event)

    def _cleanup_tmp(self):
        import os

        if self._tmp_path and os.path.exists(self._tmp_path):
            try:
                os.remove(self._tmp_path)
            except OSError:
                pass
        self._tmp_path = None

    # ── Public API ────────────────────────────────────────────────
    def set_nodes(self, nodes: Sequence[tuple]):
        """Populate from ``(name, GuiNodeDemand)`` pairs.

        Each item may also be a 3-tuple ``(name, forecast, observed)`` to
        overlay an observed reference series on the chart. Nodes without a
        (forecast) series are skipped. When more than one node has data, a
        synthetic "System total" entry (the element-wise sum) is added.
        """
        self._entries = []
        # entry = (key, label, forecast_series, observed_series_or_None)
        per_node: list[tuple] = []
        for item in nodes:
            name, demand = item[0], item[1]
            observed = item[2] if len(item) > 2 else None
            series = _series_of(demand)
            if series.size:
                obs = _series_of(observed) if observed is not None else None
                if obs is not None and obs.size == 0:
                    obs = None
                per_node.append((name, name, series, obs))

        if len(per_node) > 1:
            # Align on the shortest series for the aggregate.
            n = min(s.size for _, _, s, _ in per_node)
            total = np.sum([s[:n] for _, _, s, _ in per_node], axis=0)
            self._entries.append(
                (_SYSTEM_KEY, tr("demand_visualizer.system_total"), total, None))
        self._entries.extend(per_node)

        self._node_combo.blockSignals(True)
        self._node_combo.clear()
        for entry in self._entries:
            key, label = entry[0], entry[1]
            self._node_combo.addItem(label, key)
        self._node_combo.blockSignals(False)
        self._on_node_changed()

    def set_single(self, name: str, demand) -> bool:
        """Convenience: show one node. Returns False if it has no demand."""
        series = _series_of(demand)
        if series.size == 0:
            self.set_nodes([])
            return False
        self.set_nodes([(name, demand)])
        return True

    # ── Rendering ─────────────────────────────────────────────────
    def _current_series(self) -> Optional[np.ndarray]:
        idx = self._node_combo.currentIndex()
        if idx < 0 or idx >= len(self._entries):
            return None
        return self._entries[idx][2]

    def _current_observed(self) -> Optional[np.ndarray]:
        idx = self._node_combo.currentIndex()
        if idx < 0 or idx >= len(self._entries):
            return None
        entry = self._entries[idx]
        return entry[3] if len(entry) > 3 else None

    def _on_node_changed(self, *args):
        self._refresh()
        self._update_stats()

    def _refresh(self, *args):
        if not _WEBENGINE:
            return
        series = self._current_series()
        chart = self._chart_combo.currentData() or CHART_HOURLY
        if series is None or series.size == 0:
            fig = go.Figure()
            fig.add_annotation(text=tr("demand_visualizer.no_data"),
                               xref="paper", yref="paper", x=0.5, y=0.5,
                               showarrow=False, font=dict(size=15))
        else:
            try:
                fig = self._build_figure(chart, series, self._current_observed())
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Demand chart build failed")
                fig = go.Figure()
                fig.add_annotation(
                    text=f"{tr('demand_visualizer.error')}<br>{exc}",
                    xref="paper", yref="paper", x=0.5, y=0.5,
                    showarrow=False, font=dict(size=13, color="red"))
        fig.update_layout(**_plotly_layout())
        html = fig.to_html(include_plotlyjs=True, full_html=True,
                           config={"displaylogo": False, "responsive": True})
        # QWebEngineView.setHtml() silently drops content larger than ~2 MB;
        # Plotly with its inlined JS is ~5 MB, so write to a temp file and
        # load() it instead (no size limit, fully offline).
        self._load_html(html)

    def _load_html(self, html: str):
        import os
        import tempfile

        from PySide6.QtCore import QUrl

        if not _WEBENGINE:
            return
        fd, path = tempfile.mkstemp(suffix=".html", prefix="esfex_demand_")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(html)
        prev = getattr(self, "_tmp_path", None)
        self._tmp_path = path
        self._view.load(QUrl.fromLocalFile(path))
        if prev and os.path.exists(prev):
            try:
                os.remove(prev)
            except OSError:
                pass

    def _build_figure(self, chart: str, series: np.ndarray,
                      observed: Optional[np.ndarray] = None) -> go.Figure:
        label = self._node_combo.currentText()
        if chart == CHART_DURATION:
            return self._duration_figure(series, label, observed)
        if chart == CHART_HEATMAP:
            return self._heatmap_figure(series, label)
        return self._hourly_figure(series, label, observed)

    def _add_mean_line(self, fig, mean: float):
        fig.add_hline(
            y=mean, line=dict(color=_MEAN_COLOR, width=1.6, dash="dash"),
            annotation_text=f"{tr('demand_visualizer.mean_line')} {mean:,.0f} MW",
            annotation_position="top left",
            annotation_font=dict(color=_MEAN_COLOR, size=11))

    def _hourly_figure(self, series, label, observed=None) -> go.Figure:
        dates = _date_index(series.size, self._start_year)
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=dates, y=series, mode="lines",
            line=dict(color=_accent(), width=1), name=f"{label} (forecast)",
            hovertemplate="%{x|%Y-%m-%d %H:%M}<br>%{y:.1f} MW<extra></extra>"))
        self._add_observed_trace(fig, observed)
        self._add_mean_line(fig, float(series.mean()))
        fig.update_layout(
            title=tr("demand_visualizer.title_hourly", name=label),
            xaxis=dict(title=tr("demand_visualizer.axis_date"),
                       rangeslider=dict(visible=True), type="date"),
            yaxis=dict(title=tr("demand_visualizer.axis_mw")))
        return fig

    def _add_observed_trace(self, fig, observed):
        """Overlay a dashed observed reference series (aligned to the start)."""
        if observed is None or getattr(observed, "size", 0) == 0:
            return
        dates = _date_index(observed.size, self._start_year)
        fig.add_trace(go.Scatter(
            x=dates, y=observed, mode="lines",
            line=dict(color="#e74c3c", width=1.2, dash="dot"), name="Observed",
            hovertemplate="%{x|%Y-%m-%d %H:%M}<br>%{y:.1f} MW "
                          "(obs)<extra></extra>"))

    def _duration_figure(self, series, label, observed=None) -> go.Figure:
        ordered = np.sort(series)[::-1]
        pct = np.linspace(0, 100, ordered.size)
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=pct, y=ordered, mode="lines", fill="tozeroy",
            line=dict(color=_accent(), width=2), name=f"{label} (forecast)",
            hovertemplate="%{x:.1f}%<br>%{y:.1f} MW<extra></extra>"))
        if observed is not None and getattr(observed, "size", 0):
            obs_ord = np.sort(observed)[::-1]
            fig.add_trace(go.Scatter(
                x=np.linspace(0, 100, obs_ord.size), y=obs_ord, mode="lines",
                line=dict(color="#e74c3c", width=1.6, dash="dot"),
                name="Observed",
                hovertemplate="%{x:.1f}%<br>%{y:.1f} MW (obs)<extra></extra>"))
        self._add_mean_line(fig, float(series.mean()))
        fig.update_layout(
            title=tr("demand_visualizer.title_duration", name=label),
            xaxis=dict(title=tr("demand_visualizer.axis_pct"), range=[0, 100]),
            yaxis=dict(title=tr("demand_visualizer.axis_mw")))
        return fig

    def _heatmap_figure(self, series, label) -> go.Figure:
        n_days = series.size // 24
        if n_days < 1:
            return self._hourly_figure(series, label)
        z = series[:n_days * 24].reshape(n_days, 24).T  # (24 hours, n_days)
        # One date per day (midnight) so the x-axis reads as real dates.
        day_dates = _date_index(series.size, self._start_year)[::24][:n_days]
        fig = go.Figure(data=go.Heatmap(
            z=z, x=day_dates, y=np.arange(24),
            colorscale="Turbo", colorbar=dict(title="MW"),
            hovertemplate=("%{x|%Y-%m-%d}<br>"
                           + tr("demand_visualizer.axis_hod") + "=%{y}<br>"
                           "%{z:.1f} MW<extra></extra>")))
        fig.update_layout(
            title=tr("demand_visualizer.title_heatmap", name=label),
            xaxis=dict(title=tr("demand_visualizer.axis_date"), type="date"),
            yaxis=dict(title=tr("demand_visualizer.axis_hod"),
                       dtick=6, autorange="reversed"))
        return fig

    # ── Statistics panel ──────────────────────────────────────────
    def _update_stats(self):
        series = self._current_series()
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(8)

        header = QLabel(tr("demand_visualizer.stats_title"))
        header.setObjectName("headerLabel")
        header.setStyleSheet("font-weight: bold;")
        vbox.addWidget(header)

        if series is None or series.size == 0:
            vbox.addWidget(QLabel(tr("demand_visualizer.no_data")))
        else:
            dates = _date_index(series.size, self._start_year)
            try:
                groups = deep_stats(series, dates)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Deep stats failed")
                groups = []
            for title, rows in groups:
                gb = QGroupBox(title)
                form = QFormLayout(gb)
                form.setContentsMargins(8, 6, 8, 6)
                form.setSpacing(3)
                for name, value in rows:
                    val = QLabel(value)
                    val.setTextInteractionFlags(
                        Qt.TextInteractionFlag.TextSelectableByMouse)
                    form.addRow(QLabel(name + ":"), val)
                vbox.addWidget(gb)
        vbox.addStretch()
        self._stats_scroll.setWidget(panel)


class DemandVisualizerDialog(QDialog):
    """Standalone window wrapping :class:`DemandVisualizerWidget`."""

    def __init__(self, nodes: Sequence[tuple[str, object]], parent=None,
                 start_year: int = _DEFAULT_START_YEAR):
        super().__init__(parent)
        self.setWindowTitle(tr("demand_visualizer.window_title"))
        # Declare a normal top-level window (not Qt.Dialog): Linux window
        # managers treat dialog-type windows as non-maximizable and ignore the
        # maximize button even when its hint is present.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowSystemMenuHint
        )
        self.resize(1100, 640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.widget = DemandVisualizerWidget(self, start_year=start_year)
        layout.addWidget(self.widget)
        self.widget.set_nodes(nodes)
