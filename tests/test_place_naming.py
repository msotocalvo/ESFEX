"""Region-based node naming + placeholder replacement for the Grid Builder (#10)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from esfex.visualization.workflows.place_naming import (
    name_positions_by_region,
    place_from_address,
)


def _names(positions, **kw):
    return [n for _, _, n in name_positions_by_region(
        positions, rate_limit_s=0, **kw)]


def test_place_from_address_picks_most_specific():
    assert place_from_address(
        {"city": "Lima", "state": "Lima", "country": "Peru"}) == "Lima"
    assert place_from_address({"county": "Orange", "state": "CA"}) == "Orange"
    assert place_from_address({"state": "CA", "country": "USA"}) == "CA"
    assert place_from_address({}) is None
    # A non-place field is ignored.
    assert place_from_address({"road": "Main St"}) is None


def test_uses_geocoded_place_names():
    pos = [(1.0, 1.0, "Node 0"), (2.0, 2.0, "Node 1")]
    place = {(1.0, 1.0): "Lima", (2.0, 2.0): "Cusco"}
    assert _names(pos, geocode=lambda la, ln: place[(la, ln)]) == ["Lima", "Cusco"]


def test_duplicate_places_get_numeric_suffix():
    pos = [(1, 1, "Node 0"), (2, 2, "Node 1"), (3, 3, "Node 2")]
    assert _names(pos, geocode=lambda la, ln: "Springfield") == [
        "Springfield", "Springfield 2", "Springfield 3"]


def test_falls_back_to_generic_on_failure():
    pos = [(1, 1, "Node 0"), (2, 2, "Node 1")]

    def geo(la, ln):
        if la == 1:
            return None          # geocode returns nothing
        raise RuntimeError("boom")  # or raises

    assert _names(pos, geocode=geo) == ["Node 0", "Node 1"]


def test_cancelled_keeps_generic_names():
    pos = [(1, 1, "Node 0"), (2, 2, "Node 1")]
    assert _names(pos, geocode=lambda la, ln: "X",
                  cancelled=lambda: True) == ["Node 0", "Node 1"]


def test_empty_positions():
    assert name_positions_by_region([], rate_limit_s=0) == []


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_build_replaces_placeholder_node_no_duplicate(qapp):
    """The computed nodes replace the system's default placeholder ("Node 0"),
    rather than being appended next to it (#10 duplicate-Node-0)."""
    from esfex.visualization.data.gui_model import GuiModel
    from esfex.visualization.workflows.grid_mapping_clustering import (
        ClusterResult,
    )
    from esfex.visualization.workflows.grid_mapping_steps import (
        GridMappingBuildStep,
    )

    model = GuiModel()
    model.add_node("Node 0")  # the placeholder created with every new system
    step = GridMappingBuildStep(model=model)
    step._run_build = lambda: None  # skip the heavy OSM build

    step._on_clustering_done(ClusterResult(
        node_positions=[(10.0, -66.0, "Caracas"), (8.0, -63.0, "Guayana")],
        n_clusters=2, criterion_used="test"))

    names = [n.name for n in model.state.nodes]
    assert names == ["Caracas", "Guayana"]
    assert len(names) == len(set(names))


def test_time_budget_stops_geocoding():
    """Geocoding must stop at the wall-clock budget so the step never hangs;
    remaining nodes keep generic names."""
    import time as _t

    from esfex.visualization.workflows.place_naming import (
        name_positions_by_region,
    )

    calls = {"n": 0}

    def slow(lat, lng):
        calls["n"] += 1
        _t.sleep(0.01)
        return f"Place{int(lat)}"

    pos = [(float(i), 0.0, f"Node {i}") for i in range(40)]
    out = name_positions_by_region(
        pos, geocode=slow, rate_limit_s=0.0, time_budget_s=0.05, max_geocode=80)
    assert len(out) == 40
    assert calls["n"] < 40  # budget halted geocoding early
    assert any(n.startswith("Node ") for _, _, n in out)  # generic fallback


def test_max_geocode_cap():
    from esfex.visualization.workflows.place_naming import (
        name_positions_by_region,
    )

    calls = {"n": 0}

    def g(lat, lng):
        calls["n"] += 1
        return "X"

    pos = [(float(i), 0.0, f"Node {i}") for i in range(30)]
    name_positions_by_region(
        pos, geocode=g, rate_limit_s=0.0, time_budget_s=None, max_geocode=7)
    assert calls["n"] == 7
