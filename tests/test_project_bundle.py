"""Tests for portable project bundles (.esfexp) + config-dir path resolution."""

import json
import zipfile
from pathlib import Path

import pytest

from esfex.visualization.data import project_bundle as PB


def _write(p: Path, text="0.0\n1.0\n"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


# ── _rewrite_refs / _Bundler ──────────────────────────────────────────────

def test_rewrite_refs_bundles_and_rewrites(tmp_path):
    proj = tmp_path / "proj"
    _write(proj / "demand_0.csv")
    _write(proj / "demand_1.csv")
    _write(proj / "solar.csv")
    _write(proj / "inflow.csv")

    cfg = {
        "systems": {
            "S": {
                "demand_paths": ["demand_0.csv", "demand_1.csv"],
                "generators": {
                    "G": {
                        "Availability": "solar.csv",
                        "reservoir_inflow_file": "inflow.csv",
                    }
                },
            }
        }
    }
    staging = tmp_path / "stage"
    staging.mkdir()
    b = PB._Bundler(staging, src_base=proj)
    PB._rewrite_refs(cfg, b)

    s = cfg["systems"]["S"]
    assert s["demand_paths"] == ["demand/demand_0.csv", "demand/demand_1.csv"]
    assert s["generators"]["G"]["Availability"] == "availability/solar.csv"
    assert s["generators"]["G"]["reservoir_inflow_file"] == "inflow/inflow.csv"
    # Files actually copied into the staging tree.
    for rel in s["demand_paths"] + ["availability/solar.csv", "inflow/inflow.csv"]:
        assert (staging / rel).is_file()
    assert not b.missing


def test_rewrite_dedupes_same_source(tmp_path):
    proj = tmp_path / "proj"
    _write(proj / "shared.csv")
    cfg = {"systems": {"S": {"demand_paths": ["shared.csv", "shared.csv"]}}}
    b = PB._Bundler(tmp_path / "stage", src_base=proj)
    (tmp_path / "stage").mkdir()
    PB._rewrite_refs(cfg, b)
    # Same source → copied once, both entries point to it.
    assert cfg["systems"]["S"]["demand_paths"] == ["demand/shared.csv", "demand/shared.csv"]
    assert b.bundled.count("demand/shared.csv") == 1


def test_rewrite_basename_collision_suffixed(tmp_path):
    proj = tmp_path / "proj"
    _write(proj / "a" / "demand.csv", "1\n")
    _write(proj / "b" / "demand.csv", "2\n")
    cfg = {"systems": {"S": {"demand_paths": ["a/demand.csv", "b/demand.csv"]}}}
    b = PB._Bundler(tmp_path / "stage", src_base=proj)
    (tmp_path / "stage").mkdir()
    PB._rewrite_refs(cfg, b)
    dps = cfg["systems"]["S"]["demand_paths"]
    assert dps[0] == "demand/demand.csv"
    assert dps[1] == "demand/demand_1.csv"
    assert (b.staging / "demand/demand.csv").read_text() == "1\n"
    assert (b.staging / "demand/demand_1.csv").read_text() == "2\n"


def test_rewrite_missing_file_kept_and_reported(tmp_path):
    proj = tmp_path / "proj"
    cfg = {"systems": {"S": {"demand_paths": ["nope.csv"]}}}
    b = PB._Bundler(tmp_path / "stage", src_base=proj)
    (tmp_path / "stage").mkdir()
    PB._rewrite_refs(cfg, b)
    assert cfg["systems"]["S"]["demand_paths"] == ["nope.csv"]   # unchanged
    assert "nope.csv" in b.missing


# ── import_project + zip-slip ─────────────────────────────────────────────

def _make_esfexp(tmp_path, files: dict[str, str]) -> Path:
    """Zip ``files`` (relpath → content) into an .esfexp."""
    dest = tmp_path / "proj.esfexp"
    with zipfile.ZipFile(dest, "w") as zf:
        for rel, content in files.items():
            zf.writestr(rel, content)
    return dest


def test_import_project_roundtrip(tmp_path):
    esfexp = _make_esfexp(tmp_path, {
        "config.yaml": "systems: {}\n",
        "demand/demand_0.csv": "0\n1\n",
        "manifest.json": "{}",
    })
    out = tmp_path / "imported"
    cfg = PB.import_project(esfexp, out)
    assert cfg == out / "config.yaml"
    assert cfg.is_file()
    assert (out / "demand" / "demand_0.csv").is_file()


def test_import_rejects_zip_slip(tmp_path):
    dest = tmp_path / "evil.esfexp"
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("config.yaml", "systems: {}\n")
        zf.writestr("../escape.txt", "pwned")
    with pytest.raises(ValueError):
        PB.import_project(dest, tmp_path / "out")


def test_import_rejects_non_project_zip(tmp_path):
    esfexp = _make_esfexp(tmp_path, {"random.txt": "x"})
    with pytest.raises(ValueError):
        PB.import_project(esfexp, tmp_path / "out")


# ── GUI demand resolution: config-dir-relative with CWD fallback ──────────

def test_export_import_end_to_end(tmp_path):
    """Full path: GUI states → .esfexp → import → reloadable config with
    demand paths rewritten relative to the bundle."""
    from esfex.config.loader import load_config
    from esfex.config.schema import (
        ESFEXConfig,
        MetaNetworkConfig,
        NodeConfig,
        SystemConfig,
    )
    from esfex.visualization.data.serializer import config_to_gui_states

    proj = tmp_path / "proj"
    _write(proj / "demand_0.csv", "10\n20\n")
    _write(proj / "demand_1.csv", "30\n40\n")
    nodes = NodeConfig(num_nodes=2, nodes_connections=[0.0, 0.0, 0.0, 0.0])
    sys = SystemConfig(name="S", nodes=nodes,
                       demand_paths=["demand_0.csv", "demand_1.csv"])
    cfg = ESFEXConfig(meta_network=MetaNetworkConfig(systems=["S"]),
                      systems={"S": sys})
    states = config_to_gui_states(cfg, base_dir=str(proj))

    dest = tmp_path / "p.esfexp"
    rep = PB.export_project(states, cfg, dest, src_base=str(proj),
                            app_version="0.1.12")
    assert sorted(rep.bundled) == ["demand/demand_0.csv", "demand/demand_1.csv"]
    assert not rep.missing

    out = tmp_path / "imported"
    cfg_path = PB.import_project(dest, out)
    assert (out / "demand" / "demand_0.csv").is_file()
    assert (out / PB.MANIFEST_NAME).is_file()

    reloaded = load_config(str(cfg_path))
    assert reloaded.systems["S"].demand_paths == [
        "demand/demand_0.csv", "demand/demand_1.csv"]
    # And those relative paths resolve under the extracted config dir.
    assert (out / reloaded.systems["S"].demand_paths[0]).is_file()


def test_load_demand_csv_resolves_under_base_dir(tmp_path):
    from esfex.visualization.data.gui_model import GuiNode
    from esfex.visualization.data.serializer import _load_demand_csv

    proj = tmp_path / "proj"
    _write(proj / "demand_node_0.csv", "10\n20\n30\n")
    node = GuiNode(index=0, name="N0")

    # Found under base_dir even though CWD has no such file.
    _load_demand_csv("demand_node_0.csv", [node], base_dir=str(proj))
    assert node.demand.data == [10.0, 20.0, 30.0]

    # Without base_dir (legacy) it would look under CWD and not find it.
    node2 = GuiNode(index=0, name="N0")
    _load_demand_csv("demand_node_0.csv", [node2], base_dir=None)
    assert not node2.demand.data
