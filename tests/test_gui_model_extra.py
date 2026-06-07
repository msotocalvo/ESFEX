"""Additive tests for ``GuiModel`` (the Qt-backed signal-emitting model).

The existing ``tests/test_gui_model.py`` deliberately covers only the plain
dataclasses.  This file exercises the ``GuiModel`` mutation/query methods,
undo/redo, bulk-update, endpoint resolution, property propagation and the
cascade-delete logic, which were previously uncovered.

PySide6 is imported at module level by the target.  If a working PySide6 is
absent we install the minimal stub from ``test_gui_model.py`` so the module
imports; the ``GuiModel`` tests are skipped in that case because they rely on
real Qt signals.
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

# ---------------------------------------------------------------------------
# Ensure the target module imports even without a real PySide6.
# ---------------------------------------------------------------------------
try:
    import PySide6.QtWidgets  # noqa: F401
    _PYSIDE6_AVAILABLE = True
except Exception:
    _PYSIDE6_AVAILABLE = False
    _qtcore = ModuleType("PySide6.QtCore")
    _qtcore.QObject = type(  # type: ignore[attr-defined]
        "QObject", (), {"__init__": lambda self, *a, **kw: None}
    )
    _qtcore.Signal = lambda *a, **kw: property(lambda self: None)  # type: ignore[attr-defined]
    _pyside6 = ModuleType("PySide6")
    sys.modules.setdefault("PySide6", _pyside6)
    sys.modules.setdefault("PySide6.QtCore", _qtcore)

from esfex.visualization.data.gui_model import (  # noqa: E402
    RENEWABLE_FUELS,
    EndpointRef,
    GeoPoint,
    GuiBus,
    GuiGlobalSettings,
    GuiModel,
    GuiSystemState,
    _CheckpointSuspender,
)

# Real Qt signals are required to actually run GuiModel methods (the stub's
# Signal is a plain property with no ``.emit``).  Skip the whole module when
# PySide6 is unavailable.
pytestmark = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE, reason="GuiModel methods require a working PySide6"
)


@pytest.fixture
def model():
    """A fresh GuiModel with undo checkpoints suspended.

    Suspending checkpoints avoids the 0.3 s debounce interfering with the
    structural assertions; individual tests that need undo re-enable it.
    """
    m = GuiModel()
    m._undo_suspended = True
    return m


def _node_with_bus(m, name="N"):
    """Add a node plus an attached bus, return (node_idx, bus_id)."""
    idx = m.add_node(name)
    bus_id = m.add_bus(parent_node=idx)
    return idx, bus_id


# ======================================================================
# GuiGlobalSettings.__post_init__
# ======================================================================


class TestGlobalSettingsPostInit:
    def test_loaded_from_file_skips_preferences(self):
        # _loaded_from_file short-circuits __post_init__ so constructor
        # values are preserved verbatim.
        gs = GuiGlobalSettings(solver_name="gurobi", _loaded_from_file=True)
        assert gs.solver_name == "gurobi"

    def test_default_construction_runs_post_init(self):
        # Without _loaded_from_file the post-init runs (preferences override);
        # solver_name is lower-cased from the resolved preference default.
        gs = GuiGlobalSettings()
        assert gs.solver_name == gs.solver_name.lower()


# ======================================================================
# Construction / undo plumbing
# ======================================================================


class TestModelConstruction:
    def test_initial_state(self, model):
        assert isinstance(model.state, GuiSystemState)
        assert isinstance(model.global_settings, GuiGlobalSettings)
        assert model.stochastic_scenarios == []
        assert model.inter_system_links == []
        assert model.can_undo is False
        assert model.can_redo is False

    def test_inter_system_links_property(self, model):
        assert model.inter_system_links is model._inter_system_links


class TestRemoveLinksForSystem:
    """Deleting a system must take its inter-system links with it (#5)."""

    def test_removes_only_links_touching_system(self, model):
        model.add_inter_system_link("transmission", "A", "B", 0, 0)
        model.add_inter_system_link("fuel_route", "B", "C", 1, 1)
        keep = model.add_inter_system_link("transmission", "A", "C", 2, 2)
        removed = model.remove_links_for_system("B")
        assert len(removed) == 2
        assert [lk.link_id for lk in model.inter_system_links] == [keep]

    def test_no_links_for_unreferenced_system(self, model):
        model.add_inter_system_link("transmission", "A", "C", 0, 0)
        assert model.remove_links_for_system("Z") == []
        assert len(model.inter_system_links) == 1

    def test_emits_removed_signal(self, model):
        lid = model.add_inter_system_link("transmission", "A", "B", 0, 0)
        seen = []
        model.interSystemLinkRemoved.connect(seen.append)
        model.remove_links_for_system("A")
        assert seen == [lid]


class TestUndoRedo:
    def test_checkpoint_pushes_when_not_suspended(self):
        m = GuiModel()
        m._undo_suspended = False
        m._last_checkpoint = 0.0
        m.checkpoint()
        assert m.can_undo is True

    def test_checkpoint_suspended_noop(self):
        m = GuiModel()
        m._undo_suspended = True
        m.checkpoint()
        assert m.can_undo is False

    def test_checkpoint_debounce(self):
        m = GuiModel()
        m._undo_suspended = False
        m._last_checkpoint = 0.0
        m.checkpoint()             # accepted
        depth = len(m._undo_stack._stack)
        m.checkpoint()             # within 0.3s -> debounced, ignored
        assert len(m._undo_stack._stack) == depth

    def test_undo_empty_returns_false(self, model):
        assert model.undo() is False

    def test_redo_empty_returns_false(self, model):
        assert model.redo() is False

    def test_undo_redo_roundtrip(self):
        m = GuiModel()
        m._undo_suspended = False
        m._last_checkpoint = 0.0
        m.add_node("A")            # checkpoint pushes the empty pre-state
        assert len(m.state.nodes) == 1
        assert m.undo() is True
        assert len(m.state.nodes) == 0
        assert m.can_redo is True
        assert m.redo() is True
        assert len(m.state.nodes) == 1

    def test_clear_undo(self):
        m = GuiModel()
        m._undo_suspended = False
        m._last_checkpoint = 0.0
        m.checkpoint()
        assert m.can_undo is True
        m.clear_undo()
        assert m.can_undo is False
        assert m.can_redo is False

    def test_suspend_checkpoints_context(self):
        m = GuiModel()
        cm = m.suspend_checkpoints()
        assert isinstance(cm, _CheckpointSuspender)
        with cm:
            assert m._undo_suspended is True
            m.add_node("X")
        # On exit a single checkpoint is forced (debounce bypassed).
        assert m._undo_suspended is False
        assert m.can_undo is True


# ======================================================================
# Bulk update
# ======================================================================


class TestBulkUpdate:
    def test_nested_bulk_depth(self, model):
        model.begin_bulk_update()
        assert model._bulk_depth == 1
        model.begin_bulk_update()
        assert model._bulk_depth == 2
        model.end_bulk_update()
        assert model._bulk_depth == 1
        model.end_bulk_update()
        assert model._bulk_depth == 0

    def test_end_bulk_clamped_at_zero(self, model):
        model.end_bulk_update()
        assert model._bulk_depth == 0

    def test_emit_state_loaded_defers_inside_bulk(self, model):
        fired = []
        model.stateLoaded.connect(lambda: fired.append(1))
        model.begin_bulk_update()
        model._emit_state_loaded()
        assert fired == []                 # deferred
        assert model._bulk_dirty is True
        model.end_bulk_update()
        assert fired == [1]                 # final emit on bulk end

    def test_emit_state_loaded_immediate_outside_bulk(self, model):
        fired = []
        model.stateLoaded.connect(lambda: fired.append(1))
        model._emit_state_loaded()
        assert fired == [1]


# ======================================================================
# Inter-system links
# ======================================================================


class TestInterSystemLinks:
    def test_add_auto_id_and_kwargs(self, model):
        lid = model.add_inter_system_link(
            "transmission", "a", "b", 0, 1, capacity_mw=500.0, bogus_attr=99,
        )
        assert lid == "islink_0"
        assert model._next_islink_id == 1
        link = model.inter_system_links[0]
        assert link.capacity_mw == 500.0
        assert not hasattr(link, "bogus_attr")

    def test_add_explicit_id_advances_counter(self, model):
        model.add_inter_system_link("transmission", "a", "b", 0, 1, link_id="islink_7")
        assert model._next_islink_id == 8

    def test_add_explicit_non_numeric_id(self, model):
        model.add_inter_system_link("fuel_route", "a", "b", 0, 1, link_id="custom")
        assert model._next_islink_id == 0   # unchanged on ValueError

    def test_remove_existing(self, model):
        lid = model.add_inter_system_link("transmission", "a", "b", 0, 1)
        removed = []
        model.interSystemLinkRemoved.connect(removed.append)
        model.remove_inter_system_link(lid)
        assert model.inter_system_links == []
        assert removed == [lid]

    def test_remove_missing_no_signal(self, model):
        removed = []
        model.interSystemLinkRemoved.connect(removed.append)
        model.remove_inter_system_link("nope")
        assert removed == []

    def test_update_existing_and_missing(self, model):
        lid = model.add_inter_system_link("transmission", "a", "b", 0, 1)
        model.update_inter_system_link(lid, capacity_mw=42.0, junk=1)
        assert model.inter_system_links[0].capacity_mw == 42.0
        # Missing id is a silent no-op.
        model.update_inter_system_link("missing", capacity_mw=1.0)

    def test_clear_links_resets_counter(self, model):
        model.add_inter_system_link("transmission", "a", "b", 0, 1)
        model.clear_inter_system_links()
        assert model.inter_system_links == []
        assert model._next_islink_id == 0


# ======================================================================
# Node operations
# ======================================================================


class TestNodes:
    def test_add_node_auto_name(self, model):
        idx = model.add_node()
        assert idx == 0
        assert model.state.nodes[0].name == "Node 0"

    def test_add_node_custom_name(self, model):
        idx = model.add_node("Custom")
        assert model.state.nodes[idx].name == "Custom"

    def test_get_node_valid_and_invalid(self, model):
        idx = model.add_node("A")
        assert model.get_node(idx).name == "A"
        assert model.get_node(-1) is None
        assert model.get_node(99) is None

    def test_update_node_valid(self, model):
        idx = model.add_node("A")
        model.update_node(idx, losses=0.1, not_a_field=1)
        assert model.state.nodes[idx].losses == 0.1

    def test_update_node_out_of_range(self, model):
        model.update_node(5, losses=0.1)   # no error, no-op

    def test_remove_node_reindexes(self, model):
        model.add_node("A")
        model.add_node("B")
        model.add_node("C")
        model.remove_node(0)
        assert [n.name for n in model.state.nodes] == ["B", "C"]
        assert [n.index for n in model.state.nodes] == [0, 1]

    def test_remove_node_out_of_range_noop(self, model):
        model.add_node("A")
        model.remove_node(10)
        assert len(model.state.nodes) == 1

    def test_remove_node_cascades_equipment(self, model):
        idx, bus_id = _node_with_bus(model, "A")
        gid = model.add_generator_instance("u1", "G", "Renewable", "Sun", bus=bus_id)
        bid = model.add_battery_instance("b1", "B", bus=bus_id)
        eid = model.add_electrolyzer_instance("e1", "E", bus=bus_id)
        model.remove_node(idx)
        assert gid not in model.state.generators
        assert bid not in model.state.batteries
        assert eid not in model.state.electrolyzers
        assert bus_id not in model.state.buses

    def test_remove_node_cascades_lines_and_endpoint_reindex(self, model):
        # node0/bus0, node1/bus1, node2/bus2
        i0, b0 = _node_with_bus(model)
        i1, b1 = _node_with_bus(model)
        i2, b2 = _node_with_bus(model)
        # line between node1 and node2 (survives removal of node0) with node
        # endpoints to exercise the >index reindex branch.
        model.add_line(
            from_bus=b1, to_bus=b2,
            from_endpoint=EndpointRef("node", "1"),
            to_endpoint=EndpointRef("node", "2"),
        )
        # line touching node0 -> removed
        model.add_line(from_bus=b0, to_bus=b1)
        model.remove_node(i0)
        # one line removed, the surviving line's node endpoints decremented
        assert len(model.state.transmission_lines) == 1
        surv = model.state.transmission_lines[0]
        assert surv.from_endpoint.element_id == "0"
        assert surv.to_endpoint.element_id == "1"

    def test_remove_node_cascades_indexed_devices(self, model):
        i0, b0 = _node_with_bus(model)
        i1, b1 = _node_with_bus(model)
        model.add_transformer("T", from_bus=b0, to_bus=b0)
        model.add_acdc_converter("C", from_bus=b0, to_bus=b0)
        model.add_freq_converter("F", from_bus=b0, to_bus=b0)
        model.add_fuel_entry("FE", node=i0)
        model.add_fuel_entry("FE2", node=i1)
        model.add_fuel_route(i0, i1)
        model.remove_node(i0)
        assert model.state.transformers == []
        assert model.state.acdc_converters == []
        assert model.state.freq_converters == []
        # The fuel entry at node1 survives and is reindexed to node 0.
        assert len(model.state.fuel_entry_points) == 1
        assert model.state.fuel_entry_points[0].node == 0
        # Fuel route referencing removed node deleted.
        assert model.state.fuel_transport_routes == []


# ======================================================================
# Bus operations
# ======================================================================


class TestBuses:
    def test_add_bus_auto_id_and_name(self, model):
        bid = model.add_bus(parent_node=0)
        assert bid == "bus_0"
        assert model.state.buses[bid].name == "Bus 0"
        assert model.state._next_bus_id == 1

    def test_add_bus_explicit_id_advances_counter(self, model):
        model.add_bus(bus_id="bus_9")
        assert model.state._next_bus_id == 10

    def test_add_bus_non_numeric_id(self, model):
        model.add_bus(bus_id="custombus")
        assert model.state._next_bus_id == 0

    def test_add_bus_kwargs_applied(self, model):
        bid = model.add_bus(voltage_kv=400.0, junk=1)
        assert model.state.buses[bid].voltage_kv == 400.0

    def test_update_bus_valid_and_missing(self, model):
        bid = model.add_bus()
        model.update_bus(bid, voltage_kv=33.0, nope=1)
        assert model.state.buses[bid].voltage_kv == 33.0
        model.update_bus("missing", voltage_kv=1.0)   # no-op

    def test_get_buses_for_node(self, model):
        model.add_bus(parent_node=0)
        model.add_bus(parent_node=1)
        model.add_bus(parent_node=0)
        assert len(model.get_buses_for_node(0)) == 2
        assert len(model.get_buses_for_node(5)) == 0

    def test_remove_bus_missing_noop(self, model):
        model.remove_bus("nope")    # returns early, no error

    def test_remove_bus_cascades(self, model):
        idx, bus_id = _node_with_bus(model)
        other = model.add_bus(parent_node=idx)
        gid = model.add_generator_instance("u1", "G", "Renewable", "Sun", bus=bus_id)
        bid = model.add_battery_instance("b1", "B", bus=bus_id)
        eid = model.add_electrolyzer_instance("e1", "E", bus=bus_id)
        model.add_line(from_bus=bus_id, to_bus=other)
        model.add_transformer("T", from_bus=bus_id, to_bus=other)
        model.add_acdc_converter("C", from_bus=bus_id, to_bus=other)
        model.add_freq_converter("F", from_bus=bus_id, to_bus=other)
        model.remove_bus(bus_id)
        assert bus_id not in model.state.buses
        assert gid not in model.state.generators
        assert bid not in model.state.batteries
        assert eid not in model.state.electrolyzers
        assert model.state.transmission_lines == []
        assert model.state.transformers == []
        assert model.state.acdc_converters == []
        assert model.state.freq_converters == []


# ======================================================================
# Generator / battery / electrolyzer instance ops
# ======================================================================


class TestGenerators:
    def test_add_with_default_bus(self, model):
        idx, bus_id = _node_with_bus(model)
        gid = model.add_generator_instance(
            "u1", "G", "Renewable", "Sun", node=idx, rated_power=10.0, junk=1,
        )
        gen = model.state.generators[gid]
        assert gen.bus == bus_id
        assert gen.rated_power == 10.0

    def test_add_duplicate_unit_key_unique_id(self, model):
        _, bus_id = _node_with_bus(model)
        a = model.add_generator_instance("u1", "G", "Renewable", "Sun", bus=bus_id)
        b = model.add_generator_instance("u1", "G", "Renewable", "Sun", bus=bus_id)
        assert a != b

    def test_update_and_remove(self, model):
        _, bus_id = _node_with_bus(model)
        gid = model.add_generator_instance("u1", "G", "Renewable", "Sun", bus=bus_id)
        model.update_generator(gid, rated_power=5.0, nope=1)
        assert model.state.generators[gid].rated_power == 5.0
        model.update_generator("missing", rated_power=9.0)   # no-op
        model.remove_generator(gid)
        assert gid not in model.state.generators
        model.remove_generator("missing")   # no-op


class TestBatteries:
    def test_add_update_remove(self, model):
        idx, bus_id = _node_with_bus(model)
        bid = model.add_battery_instance("b1", "B", node=idx, capacity=100.0, junk=1)
        assert model.state.batteries[bid].capacity == 100.0
        model.update_battery(bid, capacity=50.0, nope=1)
        assert model.state.batteries[bid].capacity == 50.0
        model.update_battery("missing", capacity=1.0)
        model.remove_battery(bid)
        assert bid not in model.state.batteries
        model.remove_battery("missing")


class TestElectrolyzers:
    def test_add_update_remove(self, model):
        idx, bus_id = _node_with_bus(model)
        eid = model.add_electrolyzer_instance("e1", "E", node=idx, rated_power=20.0)
        assert model.state.electrolyzers[eid].rated_power == 20.0
        model.update_electrolyzer(eid, rated_power=10.0, nope=1)
        assert model.state.electrolyzers[eid].rated_power == 10.0
        model.update_electrolyzer("missing", rated_power=1.0)
        model.remove_electrolyzer(eid)
        assert eid not in model.state.electrolyzers
        model.remove_electrolyzer("missing")


# ======================================================================
# Line operations
# ======================================================================


class TestLines:
    def test_add_auto_id_from_nodes(self, model):
        i0, b0 = _node_with_bus(model)
        i1, b1 = _node_with_bus(model)
        lid = model.add_line(from_node=i0, to_node=i1, capacity_mw=200.0)
        assert lid == "line_0"
        assert model.state._next_line_id == 1
        ln = model.state.transmission_lines[0]
        assert ln.from_bus == b0
        assert ln.to_bus == b1
        # auto endpoints created as node refs
        assert ln.from_endpoint.element_type == "node"

    def test_add_with_endpoints_resolving_bus(self, model):
        idx, bus_id = _node_with_bus(model)
        other = model.add_bus(parent_node=idx)
        lid = model.add_line(
            from_endpoint=EndpointRef("bus", bus_id),
            to_endpoint=EndpointRef("bus", other),
        )
        ln = model.state.transmission_lines[0]
        assert ln.from_bus == bus_id
        assert ln.to_bus == other

    def test_update_existing_and_missing(self, model):
        i0, b0 = _node_with_bus(model)
        lid = model.add_line(from_bus=b0, to_bus=b0)
        model.update_line(lid, capacity_mw=300.0, nope=1)
        assert model.state.transmission_lines[0].capacity_mw == 300.0
        model.update_line("missing", capacity_mw=1.0)   # no-op

    def test_remove_existing_and_missing(self, model):
        i0, b0 = _node_with_bus(model)
        lid = model.add_line(from_bus=b0, to_bus=b0)
        model.remove_line(lid)
        assert model.state.transmission_lines == []
        model.remove_line("missing")   # no signal


# ======================================================================
# Endpoint resolution
# ======================================================================


class TestResolveEndpointNode:
    def test_node_valid_and_invalid(self, model):
        model.add_node("A")
        assert model.resolve_endpoint_node(EndpointRef("node", "0")) == 0
        assert model.resolve_endpoint_node(EndpointRef("node", "5")) is None

    def test_bus(self, model):
        idx, bus_id = _node_with_bus(model)
        assert model.resolve_endpoint_node(EndpointRef("bus", bus_id)) == idx
        assert model.resolve_endpoint_node(EndpointRef("bus", "nope")) is None

    def test_generator_battery_electrolyzer(self, model):
        idx, bus_id = _node_with_bus(model)
        gid = model.add_generator_instance("u1", "G", "Renewable", "Sun", bus=bus_id)
        bid = model.add_battery_instance("b1", "B", bus=bus_id)
        eid = model.add_electrolyzer_instance("e1", "E", bus=bus_id)
        assert model.resolve_endpoint_node(EndpointRef("generator", gid)) == idx
        assert model.resolve_endpoint_node(EndpointRef("battery", bid)) == idx
        assert model.resolve_endpoint_node(EndpointRef("electrolyzer", eid)) == idx
        assert model.resolve_endpoint_node(EndpointRef("generator", "x")) is None
        assert model.resolve_endpoint_node(EndpointRef("battery", "x")) is None
        assert model.resolve_endpoint_node(EndpointRef("electrolyzer", "x")) is None

    def test_transformer_by_index_and_name(self, model):
        idx, bus_id = _node_with_bus(model)
        model.add_transformer("TName", from_bus=bus_id, to_bus=bus_id)
        assert model.resolve_endpoint_node(EndpointRef("transformer", "0")) == idx
        # name fallback
        assert model.resolve_endpoint_node(EndpointRef("transformer", "TName")) == idx
        assert model.resolve_endpoint_node(EndpointRef("transformer", "missing")) is None

    def test_acdc_and_freq_converter(self, model):
        idx, bus_id = _node_with_bus(model)
        model.add_acdc_converter("C", from_bus=bus_id, to_bus=bus_id)
        model.add_freq_converter("F", from_bus=bus_id, to_bus=bus_id)
        assert model.resolve_endpoint_node(EndpointRef("acdc_converter", "0")) == idx
        assert model.resolve_endpoint_node(EndpointRef("freq_converter", "0")) == idx
        assert model.resolve_endpoint_node(EndpointRef("acdc_converter", "9")) is None
        assert model.resolve_endpoint_node(EndpointRef("freq_converter", "9")) is None
        assert model.resolve_endpoint_node(EndpointRef("acdc_converter", "x")) is None

    def test_fuel_entry_by_index_and_name(self, model):
        idx = model.add_node("A")
        model.add_fuel_entry("FEName", node=idx)
        assert model.resolve_endpoint_node(EndpointRef("fuel_entry", "0")) == idx
        assert model.resolve_endpoint_node(EndpointRef("fuel_entry", "FEName")) == idx
        assert model.resolve_endpoint_node(EndpointRef("fuel_entry", "missing")) is None

    def test_fuel_storage(self, model):
        idx = model.add_node("A")
        sid = model.add_fuel_storage("Tank", node=idx)
        assert model.resolve_endpoint_node(EndpointRef("fuel_storage", sid)) == idx
        assert model.resolve_endpoint_node(EndpointRef("fuel_storage", "x")) is None

    def test_unknown_type(self, model):
        assert model.resolve_endpoint_node(EndpointRef("mystery", "0")) is None

    def test_explicit_state_arg(self, model):
        st = GuiSystemState(nodes=[])
        assert model.resolve_endpoint_node(EndpointRef("node", "0"), state=st) is None


class TestResolveEndpointBus:
    def test_bus(self, model):
        _, bus_id = _node_with_bus(model)
        assert model.resolve_endpoint_bus(EndpointRef("bus", bus_id)) == bus_id
        assert model.resolve_endpoint_bus(EndpointRef("bus", "x")) is None

    def test_generator_battery_electrolyzer(self, model):
        _, bus_id = _node_with_bus(model)
        gid = model.add_generator_instance("u1", "G", "Renewable", "Sun", bus=bus_id)
        bid = model.add_battery_instance("b1", "B", bus=bus_id)
        eid = model.add_electrolyzer_instance("e1", "E", bus=bus_id)
        assert model.resolve_endpoint_bus(EndpointRef("generator", gid)) == bus_id
        assert model.resolve_endpoint_bus(EndpointRef("battery", bid)) == bus_id
        assert model.resolve_endpoint_bus(EndpointRef("electrolyzer", eid)) == bus_id
        assert model.resolve_endpoint_bus(EndpointRef("generator", "x")) is None
        assert model.resolve_endpoint_bus(EndpointRef("battery", "x")) is None
        assert model.resolve_endpoint_bus(EndpointRef("electrolyzer", "x")) is None

    def test_transformer_index_name(self, model):
        _, bus_id = _node_with_bus(model)
        model.add_transformer("TName", from_bus=bus_id, to_bus=bus_id)
        assert model.resolve_endpoint_bus(EndpointRef("transformer", "0")) == bus_id
        assert model.resolve_endpoint_bus(EndpointRef("transformer", "TName")) == bus_id
        assert model.resolve_endpoint_bus(EndpointRef("transformer", "missing")) is None

    def test_acdc_freq(self, model):
        _, bus_id = _node_with_bus(model)
        model.add_acdc_converter("C", from_bus=bus_id, to_bus=bus_id)
        model.add_freq_converter("F", from_bus=bus_id, to_bus=bus_id)
        assert model.resolve_endpoint_bus(EndpointRef("acdc_converter", "0")) == bus_id
        assert model.resolve_endpoint_bus(EndpointRef("freq_converter", "0")) == bus_id
        assert model.resolve_endpoint_bus(EndpointRef("acdc_converter", "9")) is None
        assert model.resolve_endpoint_bus(EndpointRef("freq_converter", "9")) is None
        assert model.resolve_endpoint_bus(EndpointRef("acdc_converter", "x")) is None

    def test_node_and_unknown(self, model):
        idx, bus_id = _node_with_bus(model)
        assert model.resolve_endpoint_bus(EndpointRef("node", str(idx))) == bus_id
        assert model.resolve_endpoint_bus(EndpointRef("node", "x")) is None
        assert model.resolve_endpoint_bus(EndpointRef("mystery", "0")) is None


# ======================================================================
# Connected-element queries / formatting / voltage
# ======================================================================


class TestConnectedElements:
    def test_get_connected_elements(self, model):
        idx, bus_id = _node_with_bus(model)
        model.add_transformer("T", from_bus=bus_id, to_bus=bus_id)
        # line FROM transformer 0 TO bus
        model.add_line(
            from_bus=bus_id, to_bus=bus_id,
            from_endpoint=EndpointRef("transformer", "0"),
            to_endpoint=EndpointRef("bus", bus_id),
        )
        # line TO transformer 0 FROM bus
        model.add_line(
            from_bus=bus_id, to_bus=bus_id,
            from_endpoint=EndpointRef("bus", bus_id),
            to_endpoint=EndpointRef("transformer", "0"),
        )
        conn = model.get_connected_elements("transformer", "0")
        assert ("bus", bus_id, "line_0") in conn["from"]
        assert ("bus", bus_id, "line_1") in conn["to"]

    def test_format_connected_element(self, model):
        idx, bus_id = _node_with_bus(model)
        model.state.nodes[0].name = "MyNode"
        model.state.buses[bus_id].name = "MyBus"
        gid = model.add_generator_instance("u1", "Gen!", "Renewable", "Sun", bus=bus_id)
        bid = model.add_battery_instance("b1", "Bat!", bus=bus_id)
        eid = model.add_electrolyzer_instance("e1", "Elz!", bus=bus_id)
        model.add_transformer("Tr!", from_bus=bus_id, to_bus=bus_id)
        model.add_acdc_converter("Conv!", from_bus=bus_id, to_bus=bus_id)
        model.add_freq_converter("Freq!", from_bus=bus_id, to_bus=bus_id)
        model.add_fuel_entry("FE!", node=idx)
        sid = model.add_fuel_storage("Stor!", node=idx)
        assert model.format_connected_element("node", "0") == "Node: MyNode"
        assert model.format_connected_element("bus", bus_id) == "Bus: MyBus"
        assert model.format_connected_element("generator", gid) == "Generator: Gen!"
        assert model.format_connected_element("battery", bid) == "Battery: Bat!"
        assert model.format_connected_element("electrolyzer", eid) == "Electrolyzer: Elz!"
        assert model.format_connected_element("transformer", "0") == "Transformer: Tr!"
        assert model.format_connected_element("acdc_converter", "0") == "AC/DC Converter: Conv!"
        assert model.format_connected_element("freq_converter", "0") == "Freq. Converter: Freq!"
        assert model.format_connected_element("fuel_entry", "0") == "Fuel Entry: FE!"
        assert model.format_connected_element("fuel_storage", sid) == "Fuel Storage: Stor!"

    def test_format_fallbacks(self, model):
        # Missing / non-numeric ids hit the fallback branches.
        assert model.format_connected_element("node", "x") == "Node x"
        assert model.format_connected_element("node", "99") == "Node 99"
        assert model.format_connected_element("bus", "x") == "Bus x"
        assert model.format_connected_element("generator", "x") == "Generator x"
        assert model.format_connected_element("battery", "x") == "Battery x"
        assert model.format_connected_element("electrolyzer", "x") == "Electrolyzer x"
        assert model.format_connected_element("transformer", "x") == "Transformer x"
        assert model.format_connected_element("transformer", "9") == "Transformer 9"
        assert model.format_connected_element("acdc_converter", "x") == "AC/DC Converter x"
        assert model.format_connected_element("freq_converter", "x") == "Freq. Converter x"
        assert model.format_connected_element("fuel_entry", "x") == "Fuel Entry x"
        assert model.format_connected_element("fuel_storage", "x") == "Fuel Storage x"
        assert model.format_connected_element("other", "5") == "Other 5"


class TestResolveElementVoltage:
    def test_bus(self, model):
        bid = model.add_bus(voltage_kv=132.0)
        assert model.resolve_element_voltage("bus", bid) == 132.0
        assert model.resolve_element_voltage("bus", "x") is None

    def test_generator_battery_electrolyzer(self, model):
        _, bus_id = _node_with_bus(model)
        model.update_bus(bus_id, voltage_kv=66.0)
        gid = model.add_generator_instance("u1", "G", "Renewable", "Sun", bus=bus_id)
        bid = model.add_battery_instance("b1", "B", bus=bus_id)
        eid = model.add_electrolyzer_instance("e1", "E", bus=bus_id)
        assert model.resolve_element_voltage("generator", gid) == 66.0
        assert model.resolve_element_voltage("battery", bid) == 66.0
        assert model.resolve_element_voltage("electrolyzer", eid) == 66.0
        assert model.resolve_element_voltage("generator", "x") is None
        assert model.resolve_element_voltage("battery", "x") is None
        assert model.resolve_element_voltage("electrolyzer", "x") is None

    def test_node_single_vs_multi_bus(self, model):
        idx = model.add_node("A")
        model.add_bus(parent_node=idx, voltage_kv=220.0)
        assert model.resolve_element_voltage("node", str(idx)) == 220.0
        # add a second bus -> ambiguous -> None
        model.add_bus(parent_node=idx, voltage_kv=110.0)
        assert model.resolve_element_voltage("node", str(idx)) is None
        assert model.resolve_element_voltage("node", "x") is None

    def test_transformer(self, model):
        _, bus_id = _node_with_bus(model)
        model.add_transformer("T", from_bus=bus_id, to_bus=bus_id,
                              from_voltage_kv=400.0, to_voltage_kv=220.0)
        assert model.resolve_element_voltage("transformer", "0") == 400.0
        assert model.resolve_element_voltage("transformer", "9") is None
        assert model.resolve_element_voltage("transformer", "x") is None

    def test_unknown(self, model):
        assert model.resolve_element_voltage("mystery", "0") is None


class TestResolveTransformerSideVoltages:
    def test_from_connections(self, model):
        idx, bus_id = _node_with_bus(model)
        model.update_bus(bus_id, voltage_kv=275.0)
        model.add_transformer("T", from_bus=bus_id, to_bus=bus_id)
        model.add_line(
            from_bus=bus_id, to_bus=bus_id,
            from_endpoint=EndpointRef("transformer", "0"),
            to_endpoint=EndpointRef("bus", bus_id),
        )
        from_kv, to_kv = model.resolve_transformer_side_voltages(0)
        assert from_kv == 275.0
        assert to_kv is None   # no incoming connections


# ======================================================================
# Property propagation
# ======================================================================


class TestPropagation:
    def test_propagate_bus_properties(self, model):
        idx, bus_id = _node_with_bus(model)
        model.update_bus(bus_id, voltage_kv=150.0, frequency_hz=60.0, current_type="DC")
        gid = model.add_generator_instance("u1", "G", "Renewable", "Sun", bus=bus_id)
        bid = model.add_battery_instance("b1", "B", bus=bus_id)
        model.add_line(from_bus=bus_id, to_bus=bus_id)
        model.add_transformer("T", from_bus=bus_id, to_bus=bus_id)
        model.add_acdc_converter("C", from_bus=bus_id, to_bus=bus_id)
        model.add_freq_converter("F", from_bus=bus_id, to_bus=bus_id)
        model.propagate_bus_properties(bus_id)
        assert model.state.generators[gid].frequency_hz == 60.0
        assert model.state.generators[gid].current_type == "DC"
        assert model.state.batteries[bid].current_type == "DC"
        assert model.state.transmission_lines[0].voltage_kv == 150.0
        assert model.state.transformers[0].from_voltage_kv == 150.0
        assert model.state.acdc_converters[0].from_voltage_kv == 150.0
        assert model.state.freq_converters[0].from_frequency_hz == 60.0

    def test_propagate_missing_bus_noop(self, model):
        model.propagate_bus_properties("nope")   # returns early

    def test_propagate_line_properties_uses_to_bus_fallback(self, model):
        # from_bus missing -> falls back to to_bus as the source.
        bid = model.add_bus(voltage_kv=88.0, frequency_hz=60.0, current_type="DC")
        from esfex.visualization.data.gui_model import GuiTransmissionLine
        ln = GuiTransmissionLine(line_id="x", from_bus="absent", to_bus=bid)
        model._propagate_line_properties(ln)
        assert ln.voltage_kv == 88.0
        assert ln.current_type == "DC"

    def test_propagate_line_both_missing(self, model):
        from esfex.visualization.data.gui_model import GuiTransmissionLine
        ln = GuiTransmissionLine(line_id="x", from_bus="a", to_bus="b")
        model._propagate_line_properties(ln)   # src None -> unchanged
        assert ln.voltage_kv is None

    def test_propagate_bus_to_element_each_type(self, model):
        idx, bus_id = _node_with_bus(model)
        model.update_bus(bus_id, voltage_kv=33.0, frequency_hz=60.0, current_type="DC")
        gid = model.add_generator_instance("u1", "G", "Renewable", "Sun", bus=bus_id)
        bid = model.add_battery_instance("b1", "B", bus=bus_id)
        lid = model.add_line(from_bus=bus_id, to_bus=bus_id)
        model.add_transformer("T", from_bus=bus_id, to_bus=bus_id)
        model.add_acdc_converter("C", from_bus=bus_id, to_bus=bus_id)
        model.add_freq_converter("F", from_bus=bus_id, to_bus=bus_id)

        model.propagate_bus_to_element("generator", gid)
        assert model.state.generators[gid].current_type == "DC"
        model.propagate_bus_to_element("battery", bid)
        assert model.state.batteries[bid].current_type == "DC"
        model.propagate_bus_to_element("transmission_line", lid)
        assert model.state.transmission_lines[0].voltage_kv == 33.0
        model.propagate_bus_to_element("transformer", "0")
        assert model.state.transformers[0].from_voltage_kv == 33.0
        model.propagate_bus_to_element("acdc_converter", "0")
        assert model.state.acdc_converters[0].from_voltage_kv == 33.0
        model.propagate_bus_to_element("freq_converter", "0")
        assert model.state.freq_converters[0].from_frequency_hz == 60.0

    def test_propagate_bus_to_element_missing(self, model):
        # Each branch with a missing/invalid id is a silent no-op.
        model.propagate_bus_to_element("generator", "x")
        model.propagate_bus_to_element("battery", "x")
        model.propagate_bus_to_element("transmission_line", "x")
        model.propagate_bus_to_element("transformer", "x")
        model.propagate_bus_to_element("transformer", "9")
        model.propagate_bus_to_element("acdc_converter", "x")
        model.propagate_bus_to_element("freq_converter", "x")


# ======================================================================
# Zone / fuel-entry / transformer / converter add-remove-update
# ======================================================================


class TestZones:
    def test_add_remove(self, model):
        idx = model.add_zone("Z", "Solar", [GeoPoint(0, 0)], max_capacity_mw=100.0)
        assert idx == 0
        assert model.state.development_zones[0].name == "Z"
        model.remove_zone(0)
        assert model.state.development_zones == []
        model.remove_zone(5)   # out of range no-op


class TestFuelEntry:
    def test_add_remove(self, model):
        idx = model.add_fuel_entry("Port", fuels=["LNG"], node=0, lat=1.0, lng=2.0)
        fe = model.state.fuel_entry_points[idx]
        assert fe.coordinate.lat == 1.0
        assert fe.fuels == ["LNG"]
        model.remove_fuel_entry(idx)
        assert model.state.fuel_entry_points == []
        model.remove_fuel_entry(9)   # no-op


class TestTransformerOps:
    def test_add_to_node_negative_uses_from_bus(self, model):
        _, bus_id = _node_with_bus(model)
        idx = model.add_transformer("T", from_bus=bus_id)   # to_node=-1 default
        assert model.state.transformers[idx].to_bus == bus_id

    def test_add_to_node_explicit(self, model):
        i0, b0 = _node_with_bus(model)
        i1, b1 = _node_with_bus(model)
        idx = model.add_transformer("T", from_node=i0, to_node=i1)
        assert model.state.transformers[idx].from_bus == b0
        assert model.state.transformers[idx].to_bus == b1

    def test_remove(self, model):
        _, bus_id = _node_with_bus(model)
        model.add_transformer("T", from_bus=bus_id, to_bus=bus_id)
        model.remove_transformer(0)
        assert model.state.transformers == []
        model.remove_transformer(9)   # no-op


class TestConverterDefaultBus:
    def test_acdc_default_buses_from_nodes(self, model):
        i0, b0 = _node_with_bus(model)
        i1, b1 = _node_with_bus(model)
        # No from_bus/to_bus -> resolved from node indices.
        idx = model.add_acdc_converter("C", from_node=i0, to_node=i1)
        assert model.state.acdc_converters[idx].from_bus == b0
        assert model.state.acdc_converters[idx].to_bus == b1

    def test_freq_default_buses_from_nodes(self, model):
        i0, b0 = _node_with_bus(model)
        i1, b1 = _node_with_bus(model)
        idx = model.add_freq_converter("F", from_node=i0, to_node=i1)
        assert model.state.freq_converters[idx].from_bus == b0
        assert model.state.freq_converters[idx].to_bus == b1


class TestACDCConverterOps:
    def test_add_update_remove(self, model):
        _, bus_id = _node_with_bus(model)
        idx = model.add_acdc_converter(
            "C", from_bus=bus_id, to_bus=bus_id, rated_power_mva=250.0, junk=1)
        assert model.state.acdc_converters[idx].rated_power_mva == 250.0
        model.update_acdc_converter(idx, rated_power_mva=100.0, nope=1)
        assert model.state.acdc_converters[idx].rated_power_mva == 100.0
        model.update_acdc_converter(9, rated_power_mva=1.0)   # no-op
        model.remove_acdc_converter(idx)
        assert model.state.acdc_converters == []
        model.remove_acdc_converter(9)


class TestFreqConverterOps:
    def test_add_update_remove(self, model):
        _, bus_id = _node_with_bus(model)
        idx = model.add_freq_converter(
            "F", from_bus=bus_id, to_bus=bus_id, rated_power_mva=250.0, junk=1)
        assert model.state.freq_converters[idx].rated_power_mva == 250.0
        model.update_freq_converter(idx, rated_power_mva=100.0, nope=1)
        assert model.state.freq_converters[idx].rated_power_mva == 100.0
        model.update_freq_converter(9, rated_power_mva=1.0)   # no-op
        model.remove_freq_converter(idx)
        assert model.state.freq_converters == []
        model.remove_freq_converter(9)


# ======================================================================
# Fuel source / storage / route / fuel ops
# ======================================================================


class TestFuelStorage:
    def test_add_auto_id_with_fuel(self, model):
        sid = model.add_fuel_storage("Tank", fuel="Gas", node=0)
        assert sid == "fuel_storage_0"
        st = model.state.fuel_storages[sid]
        assert st.fuels == ["Gas"]
        assert "Gas" in st.fuel_params

    def test_add_auto_id_collision(self, model):
        # Pre-seed fuel_storage_0 and fuel_storage_1 explicitly; the next auto
        # id starts at len()==2 -> "fuel_storage_2" which collides, forcing the
        # while-loop to advance to "fuel_storage_3".
        model.add_fuel_storage("A", storage_id="fuel_storage_0")
        model.add_fuel_storage("B", storage_id="fuel_storage_2")
        sid = model.add_fuel_storage("C")
        assert sid == "fuel_storage_3"

    def test_add_no_fuel(self, model):
        sid = model.add_fuel_storage("Tank", node=0, latitude=5.0)
        st = model.state.fuel_storages[sid]
        assert st.fuels == []
        assert st.latitude == 5.0

    def test_update_remove(self, model):
        sid = model.add_fuel_storage("Tank")
        model.update_fuel_storage(sid, node=2, nope=1)
        assert model.state.fuel_storages[sid].node == 2
        model.update_fuel_storage("missing", node=1)
        model.remove_fuel_storage(sid)
        assert sid not in model.state.fuel_storages
        model.remove_fuel_storage("missing")


class TestFuelRoute:
    def test_add_auto_id_and_endpoints(self, model):
        rid = model.add_fuel_route(0, 1, fuels=["LNG"], capacity=50.0)
        assert rid == "fuel_route_0"
        rt = model.state.fuel_transport_routes[0]
        assert rt.from_endpoint.element_type == "node"
        assert rt.fuels == ["LNG"]

    def test_update_existing_and_missing(self, model):
        rid = model.add_fuel_route(0, 1)
        model.update_fuel_route(rid, capacity=99.0, nope=1)
        assert model.state.fuel_transport_routes[0].capacity == 99.0
        model.update_fuel_route("missing", capacity=1.0)

    def test_remove_existing_and_missing(self, model):
        rid = model.add_fuel_route(0, 1)
        model.remove_fuel_route(rid)
        assert model.state.fuel_transport_routes == []
        model.remove_fuel_route("missing")


class TestFuelConfig:
    def test_add_update_remove(self, model):
        fid = model.add_fuel("Coal", "Coal", emission_factor=0.34, junk=1)
        assert model.state.fuels[fid].emission_factor == 0.34
        model.update_fuel(fid, emission_factor=0.5, nope=1)
        assert model.state.fuels[fid].emission_factor == 0.5
        model.update_fuel("missing", emission_factor=1.0)
        model.remove_fuel(fid)
        assert fid not in model.state.fuels
        model.remove_fuel("missing")


# ======================================================================
# Technology / investment portfolio
# ======================================================================


class TestTechnology:
    def test_add_auto_id(self, model):
        tid = model.add_technology("Solar", "Renewable", invest_cost=900.0, junk=1)
        assert tid == "tech_0"
        assert model.state._next_tech_id == 1
        assert model.state.technologies[tid].invest_cost == 900.0

    def test_add_explicit_numeric_id_advances(self, model):
        model.add_technology(tech_id="tech_7")
        assert model.state._next_tech_id == 8

    def test_add_explicit_non_numeric_id(self, model):
        model.add_technology(tech_id="custom")
        assert model.state._next_tech_id == 0

    def test_add_tech_prefixed_non_numeric_id(self, model):
        # Starts with "tech_" but suffix is not an int -> ValueError swallowed.
        model.add_technology(tech_id="tech_abc")
        assert model.state._next_tech_id == 0

    def test_update_remove(self, model):
        tid = model.add_technology("Solar")
        model.update_technology(tid, invest_cost=10.0, nope=1)
        assert model.state.technologies[tid].invest_cost == 10.0
        model.update_technology("missing", invest_cost=1.0)
        model.remove_technology(tid)
        assert tid not in model.state.technologies
        model.remove_technology("missing")


class TestInvestmentEntry:
    def test_add_auto_id(self, model):
        eid = model.add_investment_entry("Solar PV", "generator", target_key="u1",
                                         technology_id="tech_0", junk=1)
        assert eid == "inv_0"
        assert model.state._next_investment_id == 1
        assert model.state.investment_portfolio[eid].technology_id == "tech_0"

    def test_add_explicit_numeric_id_advances(self, model):
        model.add_investment_entry("X", "battery", entry_id="inv_5")
        assert model.state._next_investment_id == 6

    def test_add_explicit_non_numeric_id(self, model):
        model.add_investment_entry("X", "battery", entry_id="custom")
        assert model.state._next_investment_id == 0

    def test_add_inv_prefixed_non_numeric_id(self, model):
        # Starts with "inv_" but suffix is not an int -> ValueError swallowed.
        model.add_investment_entry("X", "battery", entry_id="inv_abc")
        assert model.state._next_investment_id == 0

    def test_update_remove(self, model):
        eid = model.add_investment_entry("X", "generator")
        model.update_investment_entry(eid, name="Y", nope=1)
        assert model.state.investment_portfolio[eid].name == "Y"
        model.update_investment_entry("missing", name="Z")
        model.remove_investment_entry(eid)
        assert eid not in model.state.investment_portfolio
        model.remove_investment_entry("missing")


# ======================================================================
# Helpers / load_state
# ======================================================================


class TestHelpers:
    def test_make_instance_id_unique(self):
        existing = {"u1_bus_0": 1, "u1_bus_0_2": 1}
        assert GuiModel._make_instance_id("u1", "bus_0", existing) == "u1_bus_0_3"

    def test_make_instance_id_first(self):
        assert GuiModel._make_instance_id("u1", "bus_0", {}) == "u1_bus_0"

    def test_default_bus_for_node_match(self, model):
        idx, bus_id = _node_with_bus(model)
        assert model._default_bus_for_node(idx) == bus_id

    def test_default_bus_for_node_fallback_first_bus(self, model):
        model.add_bus(parent_node=3, bus_id="bus_x")
        # No bus on node 0, but buses exist -> first bus returned.
        assert model._default_bus_for_node(0) == "bus_x"

    def test_default_bus_for_node_empty(self, model):
        assert model._default_bus_for_node(0) == "bus_0"

    def test_node_for_bus(self, model):
        idx, bus_id = _node_with_bus(model)
        assert model._node_for_bus(bus_id) == idx
        assert model._node_for_bus("nope") == 0

    def test_ensure_renewable_fuels(self, model):
        model._ensure_renewable_fuels()
        for fid in RENEWABLE_FUELS:
            assert fid in model.state.fuels


class TestLoadState:
    def test_load_emits_and_creates_default_buses(self, model):
        from esfex.visualization.data.gui_model import GuiNode
        fired = []
        model.stateLoaded.connect(lambda: fired.append(1))
        st = GuiSystemState(nodes=[GuiNode(index=0, name="A"),
                                   GuiNode(index=1, name="B")])
        model.load_state(st)
        assert fired == [1]
        # Default buses auto-created (one per node), renewable fuels ensured.
        assert len(model.state.buses) == 2
        for fid in RENEWABLE_FUELS:
            assert fid in model.state.fuels

    def test_load_advances_counters(self, model):
        from esfex.visualization.data.gui_model import (
            GuiTransmissionLine, GuiFuelTransportRoute,
        )
        st = GuiSystemState(
            buses={"bus_4": GuiBus(bus_id="bus_4")},
            transmission_lines=[GuiTransmissionLine(line_id="line_8")],
            fuel_transport_routes=[GuiFuelTransportRoute(route_id="fuel_route_3")],
        )
        model.load_state(st)
        assert model.state._next_line_id == 9
        assert model.state._next_fuel_route_id == 4
        assert model.state._next_bus_id == 5

    def test_load_ignores_non_numeric_ids(self, model):
        from esfex.visualization.data.gui_model import (
            GuiTransmissionLine, GuiFuelTransportRoute,
        )
        st = GuiSystemState(
            buses={"bus_abc": GuiBus(bus_id="bus_abc")},
            transmission_lines=[GuiTransmissionLine(line_id="line_abc")],
            fuel_transport_routes=[GuiFuelTransportRoute(route_id="fuel_route_abc")],
        )
        model.load_state(st)
        assert model.state._next_line_id == 0
        assert model.state._next_fuel_route_id == 0
        assert model.state._next_bus_id == 0
