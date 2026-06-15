"""FetchFinalizeWorker runs the post-fetch polygon clip + dedup off the GUI
thread, so the Grid Builder Step-1 aggregation no longer freezes the UI."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from esfex.visualization.workflows.grid_mapping_fetchers import (
    FetchFinalizeWorker,
    GridFeature,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _gen(lat, lng, cap=100.0, name="G"):
    return GridFeature(source="osm", feature_type="generator", name=name,
                       latitude=lat, longitude=lng, capacity_mw=cap)


def _run(worker):
    """Drive run() synchronously and capture the emitted payload."""
    out = {}
    worker.finished.connect(
        lambda feats, counts, timings: out.update(
            features=feats, counts=counts, timings=timings))
    worker.run()
    return out


def test_dedup_collapses_colocated_and_counts(qapp):
    feats = [_gen(35.0, 139.0, name=f"g{i}") for i in range(5)]
    feats.append(GridFeature(source="osm", feature_type="substation",
                             name="S", latitude=40.0, longitude=141.0))
    out = _run(FetchFinalizeWorker(feats, []))
    # Five co-located generators merge to one; substation untouched.
    assert out["counts"].get("generator") == 1
    assert out["counts"].get("substation") == 1
    assert "deduplicate" in out["timings"]
    assert "polygon_filter" not in out["timings"]  # no polygon → skipped


def test_polygon_clip_drops_outside_features(qapp):
    inside = _gen(0.5, 0.5, name="in")
    outside = _gen(50.0, 50.0, name="out")
    square = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]
    out = _run(FetchFinalizeWorker([inside, outside], square))
    names = {f.name for f in out["features"]}
    assert "in" in names and "out" not in names
    assert "polygon_filter" in out["timings"]


def test_empty_input_is_safe(qapp):
    out = _run(FetchFinalizeWorker([], []))
    assert out["features"] == []
    assert out["counts"] == {}


def test_progress_reports_stages_and_skips_empty(qapp):
    feats = [_gen(35.0, 139.0, name=f"g{i}") for i in range(4)]
    feats.append(GridFeature(source="osm", feature_type="substation",
                             name="S", latitude=40.0, longitude=141.0))
    msgs = []
    w = FetchFinalizeWorker(feats, [])
    w.progress.connect(lambda m: msgs.append(m))
    w.run()
    # Live, changing text per non-empty stage; no "0 batteries"/"0 lines" noise.
    assert any("generators" in m for m in msgs)
    assert any("substations" in m for m in msgs)
    assert any("Summarizing" in m for m in msgs)
    assert not any("batteries" in m for m in msgs)
    assert not any("transmission lines" in m for m in msgs)


def test_progress_announces_polygon_clip(qapp):
    square = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]
    msgs = []
    w = FetchFinalizeWorker([_gen(0.5, 0.5)], square)
    w.progress.connect(lambda m: msgs.append(m))
    w.run()
    assert any("Clipping" in m and "region" in m for m in msgs)
