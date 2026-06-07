"""Tests for the reusable demand visualizer (Grid Builder + node panel)."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    app = QApplication.instance() or QApplication([])
    yield app


class _Demand:
    """Stand-in for GuiNodeDemand (only ``.data`` is read)."""

    def __init__(self, data):
        self.data = list(data) if data is not None else None


def _series(n=8760, scale=1.0):
    h = np.arange(n)
    s = 800 + 200 * np.sin(2 * np.pi * h / 24) + 120 * np.sin(2 * np.pi * h / n)
    return (np.clip(s, 0, None) * scale).tolist()


def test_demand_stats():
    from esfex.visualization.panels.demand_visualizer import demand_stats

    arr = np.array([10.0, 20.0, 30.0, 40.0])
    s = demand_stats(arr)
    assert s["peak"] == 40.0 and s["base"] == 10.0
    assert s["mean"] == 25.0
    assert s["load_factor"] == pytest.approx(25.0 / 40.0)
    assert s["total_gwh"] == pytest.approx(100.0 / 1000.0)
    assert demand_stats(np.array([]))["hours"] == 0


def test_all_charts_build_and_serialize(_app):
    from esfex.visualization.panels.demand_visualizer import (
        CHART_DURATION,
        CHART_HEATMAP,
        CHART_HOURLY,
        DemandVisualizerWidget,
    )

    w = DemandVisualizerWidget()
    series = np.asarray(_series())
    for chart in (CHART_HOURLY, CHART_DURATION, CHART_HEATMAP):
        fig = w._build_figure(chart, series)
        html = fig.to_html(include_plotlyjs=True, full_html=True)
        assert "plotly" in html.lower() and len(html) > 50_000


def test_set_nodes_builds_system_total(_app):
    from esfex.visualization.panels.demand_visualizer import (
        DemandVisualizerWidget,
        _SYSTEM_KEY,
    )

    w = DemandVisualizerWidget()
    w.set_nodes([("A", _Demand(_series())), ("B", _Demand(_series(scale=0.5)))])
    keys = [w._node_combo.itemData(i) for i in range(w._node_combo.count())]
    assert keys[0] == _SYSTEM_KEY  # aggregate first
    assert "A" in keys and "B" in keys
    # system total == A + B at hour 0
    a0 = w._entries[1][2][0]
    b0 = w._entries[2][2][0]
    assert w._entries[0][2][0] == pytest.approx(a0 + b0)


def test_single_node_no_aggregate(_app):
    from esfex.visualization.panels.demand_visualizer import (
        DemandVisualizerWidget,
        _SYSTEM_KEY,
    )

    w = DemandVisualizerWidget()
    w.set_nodes([("Only", _Demand(_series()))])
    keys = [w._node_combo.itemData(i) for i in range(w._node_combo.count())]
    assert keys == ["Only"]  # no system-total entry for a single node
    assert _SYSTEM_KEY not in keys


def test_nodes_without_demand_skipped(_app):
    from esfex.visualization.panels.demand_visualizer import DemandVisualizerWidget

    w = DemandVisualizerWidget()
    w.set_nodes([("Empty", _Demand(None)), ("Real", _Demand(_series()))])
    keys = [w._node_combo.itemData(i) for i in range(w._node_combo.count())]
    assert keys == ["Real"]


def test_set_single_returns_false_when_empty(_app):
    from esfex.visualization.panels.demand_visualizer import DemandVisualizerWidget

    w = DemandVisualizerWidget()
    assert w.set_single("X", _Demand(None)) is False
    assert w.set_single("Y", _Demand(_series())) is True


def test_dialog_constructs(_app):
    from esfex.visualization.panels.demand_visualizer import DemandVisualizerDialog

    dlg = DemandVisualizerDialog([("A", _Demand(_series()))])
    assert dlg.widget._node_combo.count() == 1


def test_render_writes_tempfile_not_sethtml(_app):
    """Charts must render via a temp file + load() (setHtml caps at ~2 MB,
    which the inlined-Plotly HTML exceeds -> blank chart)."""
    import os

    from esfex.visualization.panels import demand_visualizer as dv

    if not dv._WEBENGINE:
        pytest.skip("QWebEngine unavailable")
    w = dv.DemandVisualizerWidget()
    w.set_nodes([("A", _Demand(_series()))])  # triggers a render
    assert w._tmp_path and os.path.exists(w._tmp_path)
    html = open(w._tmp_path, encoding="utf-8").read()
    assert len(html) > 2_000_000  # bigger than the setHtml limit
    assert "plotly" in html.lower()
    w._cleanup_tmp()
    assert w._tmp_path is None


def test_deep_stats_groups_and_values():
    from esfex.visualization.panels.demand_visualizer import (
        deep_stats, _date_index,
    )

    series = np.asarray(_series(8760))
    groups = deep_stats(series, _date_index(series.size, 2025))
    titles = [g[0] for g in groups]
    assert len(groups) == 5
    # every group carries at least a few rows
    assert all(len(rows) >= 4 for _, rows in groups)
    # overview reports the right peak
    flat = {k: v for _, rows in groups for k, v in rows}
    assert any("MW" in v for v in flat.values())
    assert deep_stats(np.asarray([]), _date_index(0, 2025)) == []


def test_hourly_has_date_axis_and_mean_line(_app):
    from esfex.visualization.panels.demand_visualizer import (
        DemandVisualizerWidget,
    )

    w = DemandVisualizerWidget(start_year=2040)
    fig = w._hourly_figure(np.asarray(_series()), "N")
    assert fig.layout.xaxis.type == "date"
    assert str(fig.data[0].x[0]).startswith("2040-01-01")
    # red dashed mean line present
    assert fig.layout.shapes and fig.layout.shapes[0].line.color == "#e74c3c"


def test_stats_panel_updates(_app):
    from esfex.visualization.panels.demand_visualizer import (
        DemandVisualizerWidget,
    )

    w = DemandVisualizerWidget()
    w.set_nodes([("A", _Demand(_series()))])
    panel = w._stats_scroll.widget()
    assert panel is not None
    # group boxes were rendered
    from PySide6.QtWidgets import QGroupBox
    assert panel.findChildren(QGroupBox)
