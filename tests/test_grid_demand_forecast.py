"""Grid Builder step 3: forecast demand is persisted to CSV + node demand (#7)."""

from types import SimpleNamespace

import numpy as np

from esfex.visualization.workflows.grid_mapping_steps import (
    write_forecast_demand_csvs,
)


def _nodes(*names):
    return [SimpleNamespace(name=n, demand=None) for n in names]


def test_writes_csv_and_populates_node_demand(tmp_path):
    nodes = _nodes("Alpha", "Beta")
    # 3 timesteps x 2 nodes.
    series = np.array([[10.0, 5.0], [20.0, 7.0], [15.0, 6.0]])
    result = SimpleNamespace(
        demand_multi_year=series, demand=None, peak_mw=[20.0, 7.0])

    out = tmp_path / "demand"
    n = write_forecast_demand_csvs(nodes, result, out)

    assert n == 2
    assert (out / "demand_Alpha.csv").exists()
    assert (out / "demand_Beta.csv").exists()

    d0 = nodes[0].demand
    assert d0.csv_path == str(out / "demand_Alpha.csv")
    assert d0.data == [10.0, 20.0, 15.0]
    assert d0.num_hours == 3
    assert d0.peak_mw == 20.0
    assert d0.total_mwh == 45.0
    # The CSV on disk holds the node's column.
    assert np.loadtxt(d0.csv_path).tolist() == [10.0, 20.0, 15.0]


def test_falls_back_to_single_year_and_derives_peak(tmp_path):
    nodes = _nodes("N")
    series = np.array([[1.0], [2.0], [1.5]])
    result = SimpleNamespace(
        demand_multi_year=None, demand=series, peak_mw=[])  # no stats

    assert write_forecast_demand_csvs(nodes, result, tmp_path / "d") == 1
    assert nodes[0].demand.peak_mw == 2.0  # max of the series
    assert nodes[0].demand.total_mwh == 4.5


def test_filenames_sanitized_and_deduplicated(tmp_path):
    # Both names sanitize to the same base "A_B".
    nodes = _nodes("A/B", "A B")
    series = np.array([[1.0, 2.0]])
    result = SimpleNamespace(demand_multi_year=series, demand=None, peak_mw=[])

    write_forecast_demand_csvs(nodes, result, tmp_path / "d")
    paths = {nodes[0].demand.csv_path, nodes[1].demand.csv_path}
    assert len(paths) == 2  # unique despite colliding sanitized names


def test_more_nodes_than_columns_is_safe(tmp_path):
    nodes = _nodes("A", "B", "C")
    series = np.array([[1.0, 2.0]])  # only two columns
    result = SimpleNamespace(demand_multi_year=series, demand=None, peak_mw=[])

    assert write_forecast_demand_csvs(nodes, result, tmp_path / "d") == 2
    assert nodes[2].demand is None  # third node left untouched


def test_no_series_is_noop(tmp_path):
    nodes = _nodes("A")
    result = SimpleNamespace(demand_multi_year=None, demand=None, peak_mw=[])
    assert write_forecast_demand_csvs(nodes, result, tmp_path / "d") == 0
    assert nodes[0].demand is None
