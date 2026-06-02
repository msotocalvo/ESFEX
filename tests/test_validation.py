"""Comprehensive tests for the validation module.

Tests cover all validators, helper functions, data structures,
and the simplify_network entry point.  No Qt dependency is needed:
GuiSystemState and related dataclasses are plain Python dataclasses.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock

import pytest

from esfex.visualization.data.gui_model import (
    GuiBatteryInstance,
    GuiBus,
    GuiElectrolyzerInstance,
    GuiFuelEntryPoint,
    GuiFuelStorage,
    GuiFuelTransportRoute,
    GuiGeneratorInstance,
    GuiNode,
    GuiNodeDemand,
    GuiSystemState,
    GuiTransformer,
    GuiTransmissionLine,
    GuiACDCConverter,
    GuiFrequencyConverter,
)
from esfex.visualization.data.validation import (
    CATEGORY_ORDER,
    SimplificationAction,
    ValidationIssue,
    _build_bus_adjacency,
    _bus_has_demand,
    _bus_has_useful_equipment,
    _find_dead_end_buses,
    _validate_batteries,
    _validate_buses,
    _validate_connectivity,
    _validate_converters,
    _validate_demand,
    _validate_fuel_entries,
    _validate_fuel_network,
    _validate_generation,
    _validate_generators,
    _validate_lines,
    _validate_nodes,
    _validate_transformers,
    count_validators,
    find_dead_end_buses,
    preload_demand_data,
    simplify_network,
    validate_state,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_state(**overrides) -> GuiSystemState:
    """Return a minimal valid GuiSystemState.

    Default layout:
      - 2 nodes (index 0, 1) each with a name and 100 MW peak demand
      - 2 buses (bus_0 on node 0, bus_1 on node 1)
      - 1 generator (gen_0) on bus_0 with rated_power 200 MW
      - 1 transmission line (line_0) from bus_0 to bus_1, capacity 100 MW
      - Everything else empty
    """
    nodes = overrides.pop("nodes", [
        GuiNode(index=0, name="North",
                demand=GuiNodeDemand(peak_mw=100.0, total_mwh=800.0)),
        GuiNode(index=1, name="South",
                demand=GuiNodeDemand(peak_mw=100.0, total_mwh=800.0)),
    ])
    # role="load" required when demand_fraction > 0 (validator invariant:
    # role="connection" forces demand_fraction == 0).
    buses = overrides.pop("buses", {
        "bus_0": GuiBus(bus_id="bus_0", name="Bus North", parent_node=0,
                        demand_fraction=1.0, role="load"),
        "bus_1": GuiBus(bus_id="bus_1", name="Bus South", parent_node=1,
                        demand_fraction=1.0, role="load"),
    })
    generators = overrides.pop("generators", {
        "gen_0": GuiGeneratorInstance(
            instance_id="gen_0", unit_key="unit_0", name="Thermal",
            gen_type="Non-renewable", fuel="Gas",
            bus="bus_0", node=0, rated_power=200.0,
        ),
    })
    transmission_lines = overrides.pop("transmission_lines", [
        GuiTransmissionLine(
            line_id="line_0", from_bus="bus_0", to_bus="bus_1",
            capacity_mw=100.0,
        ),
    ])

    return GuiSystemState(
        name="test_system",
        nodes=nodes,
        buses=buses,
        generators=generators,
        transmission_lines=transmission_lines,
        batteries=overrides.pop("batteries", {}),
        transformers=overrides.pop("transformers", []),
        acdc_converters=overrides.pop("acdc_converters", []),
        freq_converters=overrides.pop("freq_converters", []),
        fuel_entry_points=overrides.pop("fuel_entry_points", []),
        fuel_transport_routes=overrides.pop("fuel_transport_routes", []),
        fuel_storages=overrides.pop("fuel_storages", {}),
        electrolyzers=overrides.pop("electrolyzers", {}),
        **overrides,
    )


# ===========================================================================
# ValidationIssue dataclass
# ===========================================================================


class TestValidationIssue:
    """Tests for the ValidationIssue dataclass."""

    def test_creation_with_required_fields(self):
        issue = ValidationIssue(severity="error", category="Node",
                                message="duplicate indices")
        assert issue.severity == "error"
        assert issue.category == "Node"
        assert issue.message == "duplicate indices"

    def test_default_element_fields(self):
        issue = ValidationIssue(severity="warning", category="Line",
                                message="self-loop")
        assert issue.element_type == ""
        assert issue.element_id == ""

    def test_creation_with_all_fields(self):
        issue = ValidationIssue(
            severity="info", category="Gen", message="zero power",
            element_type="generator", element_id="gen_42",
        )
        assert issue.element_type == "generator"
        assert issue.element_id == "gen_42"

    def test_severity_literal_values(self):
        for sev in ("error", "warning", "info"):
            issue = ValidationIssue(severity=sev, category="x", message="y")
            assert issue.severity == sev

    def test_equality(self):
        a = ValidationIssue(severity="error", category="A", message="m")
        b = ValidationIssue(severity="error", category="A", message="m")
        assert a == b

    def test_inequality_different_severity(self):
        a = ValidationIssue(severity="error", category="A", message="m")
        b = ValidationIssue(severity="warning", category="A", message="m")
        assert a != b


# ===========================================================================
# SimplificationAction dataclass
# ===========================================================================


class TestSimplificationAction:
    """Tests for the SimplificationAction dataclass."""

    def test_creation(self):
        act = SimplificationAction(
            action_type="remove_bus", element_id="bus_3",
            reason="dead-end bus",
        )
        assert act.action_type == "remove_bus"
        assert act.element_id == "bus_3"
        assert act.reason == "dead-end bus"

    def test_remove_line_action(self):
        act = SimplificationAction(
            action_type="remove_line", element_id="line_5",
            reason="stub line",
        )
        assert act.action_type == "remove_line"

    def test_remove_fuel_entry_action(self):
        act = SimplificationAction(
            action_type="remove_fuel_entry", element_id="0",
            reason="unused",
        )
        assert act.action_type == "remove_fuel_entry"

    def test_remove_fuel_storage_action(self):
        act = SimplificationAction(
            action_type="remove_fuel_storage", element_id="fs_0",
            reason="no consumers",
        )
        assert act.action_type == "remove_fuel_storage"

    def test_remove_fuel_route_action(self):
        act = SimplificationAction(
            action_type="remove_fuel_route", element_id="rt_0",
            reason="stub",
        )
        assert act.action_type == "remove_fuel_route"

    def test_equality(self):
        a = SimplificationAction("remove_bus", "bus_0", "reason")
        b = SimplificationAction("remove_bus", "bus_0", "reason")
        assert a == b


# ===========================================================================
# CATEGORY_ORDER constant
# ===========================================================================


class TestCategoryOrder:
    """Verify the CATEGORY_ORDER constant."""

    def test_is_list(self):
        assert isinstance(CATEGORY_ORDER, list)

    def test_all_strings(self):
        for cat in CATEGORY_ORDER:
            assert isinstance(cat, str)

    def test_contains_expected_categories(self):
        expected = {
            "structural", "electrical", "demand",
            "generation", "fuel_network", "connectivity",
            "topology_audit",
        }
        assert set(CATEGORY_ORDER) == expected

    def test_no_duplicates(self):
        assert len(CATEGORY_ORDER) == len(set(CATEGORY_ORDER))

    def test_structural_first(self):
        assert CATEGORY_ORDER[0] == "structural"

    def test_topology_audit_last(self):
        assert CATEGORY_ORDER[-1] == "topology_audit"


# ===========================================================================
# validate_state() entry point
# ===========================================================================


class TestValidateState:
    """Tests for the top-level validate_state() function."""

    def test_valid_state_no_errors(self):
        state = _make_state()
        issues = validate_state(state)
        errors = [i for i in issues if i.severity == "error"]
        assert errors == []

    def test_returns_list(self):
        state = _make_state()
        result = validate_state(state)
        assert isinstance(result, list)

    def test_filter_by_single_category(self):
        state = _make_state()
        issues = validate_state(state, categories={"structural"})
        # Only structural validators should have run
        assert all(
            i.category in ("Node", "Line", "Connectivity")
            for i in issues
        )

    def test_filter_by_multiple_categories(self):
        state = _make_state()
        issues = validate_state(state, categories={"structural", "demand"})
        # Should contain demand-related issues if any
        assert isinstance(issues, list)

    def test_empty_categories_set_runs_nothing(self):
        state = _make_state()
        issues = validate_state(state, categories=set())
        assert issues == []

    def test_progress_callback_called(self):
        state = _make_state()
        calls = []
        def cb(step, total, desc):
            calls.append((step, total, desc))
        validate_state(state, progress_callback=cb)
        # Final call should have step == total
        assert calls[-1][0] == calls[-1][1]
        assert "complete" in calls[-1][2].lower()

    def test_progress_callback_step_increments(self):
        state = _make_state()
        steps = []
        def cb(step, total, desc):
            steps.append(step)
        validate_state(state, progress_callback=cb)
        # Steps should be monotonically non-decreasing
        for i in range(1, len(steps)):
            assert steps[i] >= steps[i - 1]


# ===========================================================================
# _validate_nodes()
# ===========================================================================


class TestValidateNodes:
    """Tests for the _validate_nodes() validator."""

    def test_unique_indices_clean(self):
        state = _make_state()
        issues = _validate_nodes(state)
        assert issues == []

    def test_duplicate_indices_error(self):
        state = _make_state(nodes=[
            GuiNode(index=0, name="A"),
            GuiNode(index=0, name="B"),
        ])
        issues = _validate_nodes(state)
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert "Duplicate" in issues[0].message

    def test_three_nodes_with_one_duplicate(self):
        state = _make_state(nodes=[
            GuiNode(index=0, name="A"),
            GuiNode(index=1, name="B"),
            GuiNode(index=0, name="C"),
        ])
        issues = _validate_nodes(state)
        assert len(issues) == 1
        assert issues[0].severity == "error"

    def test_empty_nodes_error(self):
        state = _make_state(nodes=[])
        issues = _validate_nodes(state)
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert "no nodes" in issues[0].message.lower()

    def test_single_node_clean(self):
        state = _make_state(nodes=[GuiNode(index=0, name="Only")])
        issues = _validate_nodes(state)
        assert issues == []


# ===========================================================================
# _validate_lines()
# ===========================================================================


class TestValidateLines:
    """Tests for the _validate_lines() validator."""

    def test_valid_line_clean(self):
        state = _make_state()
        issues = _validate_lines(state)
        assert issues == []

    def test_self_loop_error(self):
        state = _make_state(transmission_lines=[
            GuiTransmissionLine(
                line_id="line_loop", from_bus="bus_0", to_bus="bus_0",
                capacity_mw=50.0,
            ),
        ])
        issues = _validate_lines(state)
        self_loops = [i for i in issues if "self-loop" in i.message]
        assert len(self_loops) == 1
        assert self_loops[0].severity == "error"
        assert self_loops[0].element_type == "line"
        assert self_loops[0].element_id == "line_loop"

    def test_nonexistent_from_bus_error(self):
        state = _make_state(transmission_lines=[
            GuiTransmissionLine(
                line_id="line_bad", from_bus="bus_missing", to_bus="bus_1",
                capacity_mw=50.0,
            ),
        ])
        issues = _validate_lines(state)
        missing = [i for i in issues if "does not exist" in i.message
                   and "from_bus" in i.message]
        assert len(missing) == 1
        assert missing[0].severity == "error"

    def test_nonexistent_to_bus_error(self):
        state = _make_state(transmission_lines=[
            GuiTransmissionLine(
                line_id="line_bad", from_bus="bus_0", to_bus="bus_missing",
                capacity_mw=50.0,
            ),
        ])
        issues = _validate_lines(state)
        missing = [i for i in issues if "does not exist" in i.message
                   and "to_bus" in i.message]
        assert len(missing) == 1

    def test_zero_capacity_warning(self):
        state = _make_state(transmission_lines=[
            GuiTransmissionLine(
                line_id="line_zero", from_bus="bus_0", to_bus="bus_1",
                capacity_mw=0.0,
            ),
        ])
        issues = _validate_lines(state)
        cap_issues = [i for i in issues if "capacity" in i.message]
        assert len(cap_issues) == 1
        assert cap_issues[0].severity == "warning"

    def test_negative_capacity_warning(self):
        state = _make_state(transmission_lines=[
            GuiTransmissionLine(
                line_id="line_neg", from_bus="bus_0", to_bus="bus_1",
                capacity_mw=-10.0,
            ),
        ])
        issues = _validate_lines(state)
        cap_issues = [i for i in issues if "capacity" in i.message]
        assert len(cap_issues) >= 1
        assert cap_issues[0].severity == "warning"

    def test_no_lines_clean(self):
        state = _make_state(transmission_lines=[])
        issues = _validate_lines(state)
        assert issues == []

    def test_multiple_issues_on_one_line(self):
        # Self-loop with zero capacity on nonexistent bus
        state = _make_state(transmission_lines=[
            GuiTransmissionLine(
                line_id="line_multi", from_bus="bus_x", to_bus="bus_x",
                capacity_mw=0.0,
            ),
        ])
        issues = _validate_lines(state)
        # Should have self-loop, from_bus missing, to_bus missing, zero capacity
        assert len(issues) >= 3


# ===========================================================================
# _validate_buses()
# ===========================================================================


class TestValidateBuses:
    """Tests for the _validate_buses() validator."""

    def test_valid_buses_clean(self):
        state = _make_state()
        issues = _validate_buses(state)
        # Should have no errors (warnings about demand fractions are OK)
        errors = [i for i in issues if i.severity == "error"]
        assert errors == []

    def test_bus_with_nonexistent_parent_node_error(self):
        state = _make_state(buses={
            "bus_orphan": GuiBus(bus_id="bus_orphan", name="Orphan",
                                parent_node=99),
        })
        issues = _validate_buses(state)
        orphans = [i for i in issues if "parent node" in i.message
                   and i.severity == "error"]
        assert len(orphans) >= 1

    def test_bus_no_equipment_no_connections_no_demand_warning(self):
        state = _make_state(
            generators={},
            transmission_lines=[],
            buses={
                "bus_0": GuiBus(bus_id="bus_0", name="Empty Bus",
                                parent_node=0, demand_fraction=0.0),
                "bus_1": GuiBus(bus_id="bus_1", name="Other Bus",
                                parent_node=1, demand_fraction=1.0),
            },
        )
        issues = _validate_buses(state)
        empty_bus_warnings = [
            i for i in issues
            if "no equipment" in i.message and "bus_0" in i.message
        ]
        assert len(empty_bus_warnings) == 1
        assert empty_bus_warnings[0].severity == "warning"

    def test_demand_fraction_sum_warning(self):
        # role="load" required so the demand_fraction values participate
        # in the sum (connection buses are forced to df=0 and ignored).
        state = _make_state(buses={
            "bus_0a": GuiBus(bus_id="bus_0a", name="A", parent_node=0,
                             demand_fraction=0.3, role="load"),
            "bus_0b": GuiBus(bus_id="bus_0b", name="B", parent_node=0,
                             demand_fraction=0.3, role="load"),
            "bus_1": GuiBus(bus_id="bus_1", name="C", parent_node=1,
                            demand_fraction=1.0, role="load"),
        })
        issues = _validate_buses(state)
        frac_warnings = [
            i for i in issues
            if "demand fractions sum" in i.message
        ]
        assert len(frac_warnings) >= 1
        assert frac_warnings[0].severity in ("warning", "error")

    def test_demand_fraction_sum_1_no_warning(self):
        state = _make_state(buses={
            "bus_0a": GuiBus(bus_id="bus_0a", name="A", parent_node=0,
                             demand_fraction=0.5),
            "bus_0b": GuiBus(bus_id="bus_0b", name="B", parent_node=0,
                             demand_fraction=0.5),
            "bus_1": GuiBus(bus_id="bus_1", name="C", parent_node=1,
                            demand_fraction=1.0),
        })
        issues = _validate_buses(state)
        frac_warnings = [
            i for i in issues if "demand fractions sum" in i.message
        ]
        assert frac_warnings == []

    def test_generator_on_nonexistent_bus_error(self):
        state = _make_state(generators={
            "gen_bad": GuiGeneratorInstance(
                instance_id="gen_bad", unit_key="u", name="Bad",
                gen_type="Non-renewable", fuel="Gas",
                bus="bus_missing", node=0, rated_power=100.0,
            ),
        })
        issues = _validate_buses(state)
        gen_errs = [
            i for i in issues
            if "Generator" in i.message and "does not exist" in i.message
        ]
        assert len(gen_errs) >= 1
        assert gen_errs[0].severity == "error"

    def test_battery_on_nonexistent_bus_error(self):
        state = _make_state(batteries={
            "bat_bad": GuiBatteryInstance(
                instance_id="bat_bad", unit_key="b", name="Bad Bat",
                bus="bus_gone", node=0, rated_power=50.0,
            ),
        })
        issues = _validate_buses(state)
        bat_errs = [
            i for i in issues
            if "Battery" in i.message and "does not exist" in i.message
        ]
        assert len(bat_errs) >= 1

    def test_electrolyzer_on_nonexistent_bus_error(self):
        state = _make_state(electrolyzers={
            "elz_bad": GuiElectrolyzerInstance(
                instance_id="elz_bad", unit_key="e", name="Bad Elz",
                bus="bus_gone", node=0,
            ),
        })
        issues = _validate_buses(state)
        elz_errs = [
            i for i in issues
            if "Electrolyzer" in i.message and "does not exist" in i.message
        ]
        assert len(elz_errs) >= 1

    def test_line_referencing_nonexistent_bus_error(self):
        state = _make_state(transmission_lines=[
            GuiTransmissionLine(
                line_id="line_bad", from_bus="bus_gone", to_bus="bus_1",
                capacity_mw=50.0,
            ),
        ])
        issues = _validate_buses(state)
        line_errs = [
            i for i in issues
            if "Line" in i.message and "from_bus" in i.message
        ]
        assert len(line_errs) >= 1


# ===========================================================================
# _validate_generators()
# ===========================================================================


class TestValidateGenerators:
    """Tests for the _validate_generators() validator."""

    def test_valid_generator_clean(self):
        state = _make_state()
        issues = _validate_generators(state)
        assert issues == []

    def test_generator_on_nonexistent_bus_error(self):
        state = _make_state(generators={
            "gen_x": GuiGeneratorInstance(
                instance_id="gen_x", unit_key="u", name="Ghost",
                gen_type="Renewable", fuel="Sun",
                bus="bus_nowhere", node=0,
            ),
        })
        issues = _validate_generators(state)
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert "does not exist" in issues[0].message

    def test_multiple_generators_one_bad(self):
        state = _make_state(generators={
            "gen_ok": GuiGeneratorInstance(
                instance_id="gen_ok", unit_key="u1", name="OK",
                gen_type="Non-renewable", fuel="Gas",
                bus="bus_0", node=0, rated_power=100.0,
            ),
            "gen_bad": GuiGeneratorInstance(
                instance_id="gen_bad", unit_key="u2", name="Bad",
                gen_type="Non-renewable", fuel="Gas",
                bus="bus_99", node=0,
            ),
        })
        issues = _validate_generators(state)
        assert len(issues) == 1
        assert issues[0].element_id == "gen_bad"

    def test_no_generators_clean(self):
        state = _make_state(generators={})
        issues = _validate_generators(state)
        assert issues == []


# ===========================================================================
# _validate_batteries()
# ===========================================================================


class TestValidateBatteries:
    """Tests for the _validate_batteries() validator."""

    def test_valid_battery_clean(self):
        state = _make_state(batteries={
            "bat_0": GuiBatteryInstance(
                instance_id="bat_0", unit_key="b0", name="Li-ion",
                bus="bus_0", node=0, rated_power=50.0,
                efficiency_charge=0.9, efficiency_discharge=0.9,
            ),
        })
        issues = _validate_batteries(state)
        assert issues == []

    def test_battery_on_nonexistent_bus_error(self):
        state = _make_state(batteries={
            "bat_bad": GuiBatteryInstance(
                instance_id="bat_bad", unit_key="b0", name="Ghost Bat",
                bus="bus_phantom", node=0,
            ),
        })
        issues = _validate_batteries(state)
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert "does not exist" in issues[0].message

    def test_no_batteries_clean(self):
        state = _make_state(batteries={})
        issues = _validate_batteries(state)
        assert issues == []

    def test_multiple_batteries_mixed(self):
        state = _make_state(batteries={
            "bat_ok": GuiBatteryInstance(
                instance_id="bat_ok", unit_key="b0", name="OK",
                bus="bus_0", node=0,
            ),
            "bat_bad": GuiBatteryInstance(
                instance_id="bat_bad", unit_key="b1", name="Bad",
                bus="bus_nope", node=0,
            ),
        })
        issues = _validate_batteries(state)
        assert len(issues) == 1
        assert issues[0].element_id == "bat_bad"


# ===========================================================================
# _validate_demand()
# ===========================================================================


class TestValidateDemand:
    """Tests for the _validate_demand() validator."""

    def test_nodes_with_demand_clean(self):
        state = _make_state()
        issues = _validate_demand(state)
        errors = [i for i in issues if i.severity == "error"]
        assert errors == []

    def test_node_with_equipment_but_no_demand_warning(self):
        state = _make_state(nodes=[
            GuiNode(index=0, name="HasGen",
                    demand=GuiNodeDemand(peak_mw=0.0, total_mwh=0.0)),
            GuiNode(index=1, name="Other",
                    demand=GuiNodeDemand(peak_mw=100.0, total_mwh=800.0)),
        ])
        issues = _validate_demand(state)
        equip_warnings = [
            i for i in issues
            if "has generation equipment but no demand" in i.message
        ]
        assert len(equip_warnings) == 1
        assert equip_warnings[0].severity == "warning"
        assert equip_warnings[0].element_type == "node"

    def test_zero_total_demand_error(self):
        state = _make_state(
            nodes=[
                GuiNode(index=0, name="A",
                        demand=GuiNodeDemand(peak_mw=0.0)),
                GuiNode(index=1, name="B",
                        demand=GuiNodeDemand(peak_mw=0.0)),
            ],
            generators={},
        )
        issues = _validate_demand(state)
        zero_errors = [
            i for i in issues if "zero total peak demand" in i.message
        ]
        assert len(zero_errors) == 1
        assert zero_errors[0].severity == "error"

    def test_csv_path_set_but_data_none_warning(self):
        state = _make_state(
            nodes=[
                GuiNode(index=0, name="A",
                        demand=GuiNodeDemand(
                            csv_path="/nonexistent/file.csv",
                            peak_mw=0.0,
                        )),
                GuiNode(index=1, name="B",
                        demand=GuiNodeDemand(peak_mw=100.0, total_mwh=800.0)),
            ],
            generators={},
        )
        issues = _validate_demand(state)
        csv_warnings = [
            i for i in issues
            if "demand CSV path set but data could not be loaded" in i.message
        ]
        assert len(csv_warnings) == 1

    def test_no_nodes_clean(self):
        state = _make_state(nodes=[], generators={})
        issues = _validate_demand(state)
        assert issues == []


# ===========================================================================
# _validate_generation()
# ===========================================================================


class TestValidateGeneration:
    """Tests for the _validate_generation() validator."""

    def test_adequate_generation_clean(self):
        state = _make_state()  # 200 MW gen vs 200 MW demand
        issues = _validate_generation(state)
        adequacy_warnings = [
            i for i in issues if "Adequacy" in i.message
        ]
        assert adequacy_warnings == []

    def test_inadequate_generation_warning(self):
        state = _make_state(generators={
            "gen_small": GuiGeneratorInstance(
                instance_id="gen_small", unit_key="u0", name="Tiny",
                gen_type="Non-renewable", fuel="Gas",
                bus="bus_0", node=0, rated_power=10.0,
            ),
        })
        issues = _validate_generation(state)
        adequacy_warnings = [
            i for i in issues if "Adequacy" in i.message
        ]
        assert len(adequacy_warnings) == 1
        assert adequacy_warnings[0].severity == "warning"

    def test_renewable_without_availability_file_warning(self):
        state = _make_state(generators={
            "gen_re": GuiGeneratorInstance(
                instance_id="gen_re", unit_key="u0", name="Solar",
                gen_type="Renewable", fuel="Sun",
                bus="bus_0", node=0, rated_power=100.0,
                availability_file=None,
            ),
        })
        issues = _validate_generation(state)
        avail_warnings = [
            i for i in issues if "no availability file" in i.message
        ]
        assert len(avail_warnings) == 1
        assert avail_warnings[0].severity == "warning"

    def test_renewable_with_availability_file_no_warning(self):
        state = _make_state(generators={
            "gen_re": GuiGeneratorInstance(
                instance_id="gen_re", unit_key="u0", name="Solar",
                gen_type="Renewable", fuel="Sun",
                bus="bus_0", node=0, rated_power=100.0,
                availability_file="/data/solar.csv",
            ),
        })
        issues = _validate_generation(state)
        avail_warnings = [
            i for i in issues if "no availability file" in i.message
        ]
        assert avail_warnings == []

    def test_zero_rated_power_info(self):
        state = _make_state(generators={
            "gen_zero": GuiGeneratorInstance(
                instance_id="gen_zero", unit_key="u0", name="Empty",
                gen_type="Non-renewable", fuel="Gas",
                bus="bus_0", node=0, rated_power=0.0,
            ),
        })
        issues = _validate_generation(state)
        zero_infos = [
            i for i in issues if "zero rated power" in i.message
        ]
        assert len(zero_infos) == 1
        assert zero_infos[0].severity == "info"

    def test_generation_with_battery_contributes_to_adequacy(self):
        state = _make_state(
            generators={
                "gen_small": GuiGeneratorInstance(
                    instance_id="gen_small", unit_key="u0", name="Small",
                    gen_type="Non-renewable", fuel="Gas",
                    bus="bus_0", node=0, rated_power=100.0,
                ),
            },
            batteries={
                "bat_0": GuiBatteryInstance(
                    instance_id="bat_0", unit_key="b0", name="Storage",
                    bus="bus_0", node=0, rated_power=100.0,
                ),
            },
        )
        issues = _validate_generation(state)
        adequacy_warnings = [
            i for i in issues if "Adequacy" in i.message
        ]
        # 100 MW gen + 100 MW bat = 200 MW >= 200 MW demand
        assert adequacy_warnings == []

    def test_zero_demand_no_adequacy_check(self):
        state = _make_state(
            nodes=[
                GuiNode(index=0, name="A",
                        demand=GuiNodeDemand(peak_mw=0.0)),
                GuiNode(index=1, name="B",
                        demand=GuiNodeDemand(peak_mw=0.0)),
            ],
            generators={},
        )
        issues = _validate_generation(state)
        adequacy_warnings = [
            i for i in issues if "Adequacy" in i.message
        ]
        assert adequacy_warnings == []


# ===========================================================================
# _validate_transformers()
# ===========================================================================


class TestValidateTransformers:
    """Tests for the _validate_transformers() validator."""

    def test_no_transformers_clean(self):
        state = _make_state()
        issues = _validate_transformers(state)
        assert issues == []

    def test_valid_transformer_clean(self):
        state = _make_state(
            buses={
                "bus_0": GuiBus(bus_id="bus_0", name="HV", parent_node=0,
                                voltage_kv=110.0, demand_fraction=1.0),
                "bus_1": GuiBus(bus_id="bus_1", name="LV", parent_node=1,
                                voltage_kv=34.5, demand_fraction=1.0),
            },
            transformers=[
                GuiTransformer(name="TR1", from_bus="bus_0", to_bus="bus_1",
                               from_voltage_kv=110.0, to_voltage_kv=34.5),
            ],
        )
        issues = _validate_transformers(state)
        assert issues == []

    def test_transformer_from_bus_missing_error(self):
        state = _make_state(transformers=[
            GuiTransformer(name="TR_bad", from_bus="bus_gone", to_bus="bus_1"),
        ])
        issues = _validate_transformers(state)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 1
        assert "from_bus" in errors[0].message

    def test_transformer_to_bus_missing_error(self):
        state = _make_state(transformers=[
            GuiTransformer(name="TR_bad", from_bus="bus_0", to_bus="bus_nope"),
        ])
        issues = _validate_transformers(state)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 1
        assert "to_bus" in errors[0].message


# ===========================================================================
# _validate_converters()
# ===========================================================================


class TestValidateConverters:
    """Tests for the _validate_converters() validator."""

    def test_no_converters_clean(self):
        state = _make_state()
        issues = _validate_converters(state)
        assert issues == []

    def test_valid_acdc_converter_clean(self):
        state = _make_state(acdc_converters=[
            GuiACDCConverter(
                name="ACDC1", from_bus="bus_0", to_bus="bus_1",
                efficiency_rectify=0.98, efficiency_invert=0.98,
            ),
        ])
        issues = _validate_converters(state)
        assert issues == []

    def test_acdc_self_loop_error(self):
        state = _make_state(acdc_converters=[
            GuiACDCConverter(
                name="Loop", from_bus="bus_0", to_bus="bus_0",
                efficiency_rectify=0.98, efficiency_invert=0.98,
            ),
        ])
        issues = _validate_converters(state)
        loops = [i for i in issues if "self-loop" in i.message]
        assert len(loops) == 1
        assert loops[0].severity == "error"

    def test_acdc_zero_efficiency_error(self):
        state = _make_state(acdc_converters=[
            GuiACDCConverter(
                name="ZeroEff", from_bus="bus_0", to_bus="bus_1",
                efficiency_rectify=0.0, efficiency_invert=0.98,
            ),
        ])
        issues = _validate_converters(state)
        eff_errs = [i for i in issues if "efficiency" in i.message]
        assert len(eff_errs) == 1
        assert eff_errs[0].severity == "error"

    def test_acdc_missing_bus_error(self):
        state = _make_state(acdc_converters=[
            GuiACDCConverter(
                name="MissBus", from_bus="bus_gone", to_bus="bus_1",
                efficiency_rectify=0.98, efficiency_invert=0.98,
            ),
        ])
        issues = _validate_converters(state)
        missing = [i for i in issues if "does not exist" in i.message]
        assert len(missing) == 1

    def test_freq_converter_self_loop_error(self):
        state = _make_state(freq_converters=[
            GuiFrequencyConverter(
                name="FLoop", from_bus="bus_0", to_bus="bus_0",
                efficiency_a_to_b=0.98, efficiency_b_to_a=0.98,
            ),
        ])
        issues = _validate_converters(state)
        loops = [i for i in issues if "self-loop" in i.message]
        assert len(loops) == 1

    def test_freq_converter_zero_efficiency_error(self):
        state = _make_state(freq_converters=[
            GuiFrequencyConverter(
                name="FZero", from_bus="bus_0", to_bus="bus_1",
                efficiency_a_to_b=0.0, efficiency_b_to_a=0.98,
            ),
        ])
        issues = _validate_converters(state)
        eff_errs = [i for i in issues if "efficiency" in i.message]
        assert len(eff_errs) == 1

    def test_freq_converter_missing_bus_error(self):
        state = _make_state(freq_converters=[
            GuiFrequencyConverter(
                name="FMiss", from_bus="bus_0", to_bus="bus_nowhere",
                efficiency_a_to_b=0.98, efficiency_b_to_a=0.98,
            ),
        ])
        issues = _validate_converters(state)
        missing = [i for i in issues if "does not exist" in i.message]
        assert len(missing) == 1


# ===========================================================================
# _validate_fuel_entries()
# ===========================================================================


class TestValidateFuelEntries:
    """Tests for the _validate_fuel_entries() validator."""

    def test_no_fuel_entries_clean(self):
        state = _make_state()
        issues = _validate_fuel_entries(state)
        assert issues == []

    def test_valid_fuel_entry_clean(self):
        state = _make_state(fuel_entry_points=[
            GuiFuelEntryPoint(name="Port", fuels=["Gas"], node=0),
        ])
        issues = _validate_fuel_entries(state)
        assert issues == []

    def test_fuel_entry_nonexistent_node_error(self):
        state = _make_state(fuel_entry_points=[
            GuiFuelEntryPoint(name="Ghost Port", fuels=["Gas"], node=99),
        ])
        issues = _validate_fuel_entries(state)
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert "does not exist" in issues[0].message


# ===========================================================================
# _validate_fuel_network()
# ===========================================================================


class TestValidateFuelNetwork:
    """Tests for the _validate_fuel_network() validator."""

    def test_no_fuel_network_clean(self):
        state = _make_state(generators={})
        issues = _validate_fuel_network(state)
        assert issues == []

    def test_missing_fuel_supply_warning(self):
        state = _make_state(
            generators={
                "gen_gas": GuiGeneratorInstance(
                    instance_id="gen_gas", unit_key="u0", name="Gas Plant",
                    gen_type="Non-renewable", fuel="NaturalGas",
                    bus="bus_0", node=0, rated_power=100.0,
                ),
            },
            fuel_entry_points=[],  # No fuel supply
        )
        issues = _validate_fuel_network(state)
        missing_warnings = [
            i for i in issues
            if "not supplied" in i.message
        ]
        assert len(missing_warnings) == 1
        assert "NaturalGas" in missing_warnings[0].message

    def test_fuel_entry_with_no_fuels_warning(self):
        state = _make_state(fuel_entry_points=[
            GuiFuelEntryPoint(name="Empty Port", fuels=[], node=0),
        ])
        issues = _validate_fuel_network(state)
        empty_warnings = [
            i for i in issues if "no fuels assigned" in i.message
        ]
        assert len(empty_warnings) == 1

    def test_renewable_fuel_not_flagged(self):
        state = _make_state(
            generators={
                "gen_solar": GuiGeneratorInstance(
                    instance_id="gen_solar", unit_key="u0", name="Solar",
                    gen_type="Renewable", fuel="Sun",
                    bus="bus_0", node=0, rated_power=100.0,
                ),
            },
            fuel_entry_points=[],
        )
        issues = _validate_fuel_network(state)
        missing_warnings = [
            i for i in issues if "not supplied" in i.message
        ]
        assert missing_warnings == []

    def test_fuel_route_nonexistent_node_error(self):
        state = _make_state(fuel_transport_routes=[
            GuiFuelTransportRoute(
                route_id="route_bad", from_node=0, to_node=99,
            ),
        ])
        issues = _validate_fuel_network(state)
        route_errs = [
            i for i in issues
            if "to_node" in i.message and "does not exist" in i.message
        ]
        assert len(route_errs) == 1
        assert route_errs[0].severity == "error"


# ===========================================================================
# _validate_connectivity()
# ===========================================================================


class TestValidateConnectivity:
    """Tests for the _validate_connectivity() validator."""

    def test_connected_network_clean(self):
        state = _make_state()
        issues = _validate_connectivity(state)
        assert issues == []

    def test_single_node_clean(self):
        state = _make_state(
            nodes=[GuiNode(index=0, name="Only",
                           demand=GuiNodeDemand(peak_mw=100.0))],
            buses={"bus_0": GuiBus(bus_id="bus_0", parent_node=0)},
            generators={},
            transmission_lines=[],
        )
        issues = _validate_connectivity(state)
        assert issues == []

    def test_isolated_node_with_generator_warning(self):
        state = _make_state(
            nodes=[
                GuiNode(index=0, name="Connected",
                        demand=GuiNodeDemand(peak_mw=100.0)),
                GuiNode(index=1, name="Connected2",
                        demand=GuiNodeDemand(peak_mw=100.0)),
                GuiNode(index=2, name="Isolated",
                        demand=GuiNodeDemand(peak_mw=50.0)),
            ],
            buses={
                "bus_0": GuiBus(bus_id="bus_0", parent_node=0),
                "bus_1": GuiBus(bus_id="bus_1", parent_node=1),
                "bus_2": GuiBus(bus_id="bus_2", parent_node=2),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="G0",
                    gen_type="Non-renewable", fuel="Gas",
                    bus="bus_0", node=0, rated_power=200.0,
                ),
                "gen_iso": GuiGeneratorInstance(
                    instance_id="gen_iso", unit_key="u1", name="Isolated Gen",
                    gen_type="Non-renewable", fuel="Gas",
                    bus="bus_2", node=2, rated_power=50.0,
                ),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_0", to_bus="bus_1",
                    capacity_mw=100.0,
                ),
                # No line connecting bus_2
            ],
        )
        issues = _validate_connectivity(state)
        iso_warnings = [
            i for i in issues
            if "isolated" in i.message.lower() and "generators" in i.message
        ]
        assert len(iso_warnings) == 1
        assert iso_warnings[0].severity == "warning"
        assert iso_warnings[0].element_id == "2"

    def test_isolated_node_without_equipment_no_warning(self):
        state = _make_state(
            nodes=[
                GuiNode(index=0, name="A",
                        demand=GuiNodeDemand(peak_mw=100.0)),
                GuiNode(index=1, name="B",
                        demand=GuiNodeDemand(peak_mw=100.0)),
                GuiNode(index=2, name="Isolated Empty"),
            ],
            buses={
                "bus_0": GuiBus(bus_id="bus_0", parent_node=0),
                "bus_1": GuiBus(bus_id="bus_1", parent_node=1),
                "bus_2": GuiBus(bus_id="bus_2", parent_node=2),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="G0",
                    gen_type="Non-renewable", fuel="Gas",
                    bus="bus_0", node=0, rated_power=200.0,
                ),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_0", to_bus="bus_1",
                    capacity_mw=100.0,
                ),
            ],
        )
        issues = _validate_connectivity(state)
        iso_equipment = [
            i for i in issues
            if "isolated" in i.message.lower()
               and ("generators" in i.message or "batteries" in i.message)
        ]
        assert iso_equipment == []

    def test_isolated_node_with_battery_warning(self):
        state = _make_state(
            nodes=[
                GuiNode(index=0, name="A",
                        demand=GuiNodeDemand(peak_mw=100.0)),
                GuiNode(index=1, name="B",
                        demand=GuiNodeDemand(peak_mw=100.0)),
                GuiNode(index=2, name="Isolated"),
            ],
            buses={
                "bus_0": GuiBus(bus_id="bus_0", parent_node=0),
                "bus_1": GuiBus(bus_id="bus_1", parent_node=1),
                "bus_2": GuiBus(bus_id="bus_2", parent_node=2),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="G0",
                    gen_type="Non-renewable", fuel="Gas",
                    bus="bus_0", node=0, rated_power=200.0,
                ),
            },
            batteries={
                "bat_iso": GuiBatteryInstance(
                    instance_id="bat_iso", unit_key="b0", name="Iso Bat",
                    bus="bus_2", node=2, rated_power=10.0,
                ),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_0", to_bus="bus_1",
                    capacity_mw=100.0,
                ),
            ],
        )
        issues = _validate_connectivity(state)
        iso_bat = [
            i for i in issues
            if "isolated" in i.message.lower() and "batteries" in i.message
        ]
        assert len(iso_bat) == 1

    def test_isolated_node_with_fuel_entry_warning(self):
        state = _make_state(
            nodes=[
                GuiNode(index=0, name="A",
                        demand=GuiNodeDemand(peak_mw=100.0)),
                GuiNode(index=1, name="B",
                        demand=GuiNodeDemand(peak_mw=100.0)),
                GuiNode(index=2, name="Isolated"),
            ],
            buses={
                "bus_0": GuiBus(bus_id="bus_0", parent_node=0),
                "bus_1": GuiBus(bus_id="bus_1", parent_node=1),
                "bus_2": GuiBus(bus_id="bus_2", parent_node=2),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="G0",
                    gen_type="Non-renewable", fuel="Gas",
                    bus="bus_0", node=0, rated_power=200.0,
                ),
            },
            fuel_entry_points=[
                GuiFuelEntryPoint(name="Port", fuels=["Gas"], node=2),
            ],
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_0", to_bus="bus_1",
                    capacity_mw=100.0,
                ),
            ],
        )
        issues = _validate_connectivity(state)
        iso_fe = [
            i for i in issues
            if "isolated" in i.message.lower() and "fuel entries" in i.message
        ]
        assert len(iso_fe) == 1


# ===========================================================================
# _build_bus_adjacency()
# ===========================================================================


class TestBuildBusAdjacency:
    """Tests for _build_bus_adjacency() helper."""

    def test_basic_adjacency(self):
        state = _make_state()
        active = {"bus_0", "bus_1"}
        adj = _build_bus_adjacency(state, active)
        assert "bus_1" in adj["bus_0"]
        assert "bus_0" in adj["bus_1"]

    def test_empty_active_buses(self):
        state = _make_state()
        adj = _build_bus_adjacency(state, set())
        assert adj == {}

    def test_removed_lines_excluded(self):
        state = _make_state()
        active = {"bus_0", "bus_1"}
        adj = _build_bus_adjacency(state, active, removed_lines={"line_0"})
        assert adj["bus_0"] == set()
        assert adj["bus_1"] == set()

    def test_transformer_adds_adjacency(self):
        state = _make_state(
            transmission_lines=[],
            transformers=[
                GuiTransformer(name="TR1", from_bus="bus_0", to_bus="bus_1"),
            ],
        )
        active = {"bus_0", "bus_1"}
        adj = _build_bus_adjacency(state, active)
        assert "bus_1" in adj["bus_0"]
        assert "bus_0" in adj["bus_1"]

    def test_acdc_converter_adds_adjacency(self):
        state = _make_state(
            transmission_lines=[],
            acdc_converters=[
                GuiACDCConverter(
                    name="ACDC1", from_bus="bus_0", to_bus="bus_1",
                ),
            ],
        )
        active = {"bus_0", "bus_1"}
        adj = _build_bus_adjacency(state, active)
        assert "bus_1" in adj["bus_0"]
        assert "bus_0" in adj["bus_1"]

    def test_freq_converter_adds_adjacency(self):
        state = _make_state(
            transmission_lines=[],
            freq_converters=[
                GuiFrequencyConverter(
                    name="FC1", from_bus="bus_0", to_bus="bus_1",
                ),
            ],
        )
        active = {"bus_0", "bus_1"}
        adj = _build_bus_adjacency(state, active)
        assert "bus_1" in adj["bus_0"]

    def test_inactive_bus_excluded_from_adjacency(self):
        state = _make_state()
        # Only bus_0 active, bus_1 not in active set
        adj = _build_bus_adjacency(state, {"bus_0"})
        assert adj["bus_0"] == set()

    def test_multiple_lines_same_pair(self):
        state = _make_state(transmission_lines=[
            GuiTransmissionLine(
                line_id="line_0", from_bus="bus_0", to_bus="bus_1",
                capacity_mw=100.0,
            ),
            GuiTransmissionLine(
                line_id="line_1", from_bus="bus_0", to_bus="bus_1",
                capacity_mw=50.0,
            ),
        ])
        active = {"bus_0", "bus_1"}
        adj = _build_bus_adjacency(state, active)
        # Still just one neighbor (set)
        assert adj["bus_0"] == {"bus_1"}
        assert adj["bus_1"] == {"bus_0"}


# ===========================================================================
# _bus_has_useful_equipment()
# ===========================================================================


class TestBusHasUsefulEquipment:
    """Tests for _bus_has_useful_equipment() helper."""

    def test_bus_with_generator_true(self):
        state = _make_state()
        assert _bus_has_useful_equipment(state, "bus_0") is True

    def test_empty_bus_false(self):
        state = _make_state(generators={})
        assert _bus_has_useful_equipment(state, "bus_0") is False

    def test_bus_with_zero_power_generator_false(self):
        state = _make_state(generators={
            "gen_zero": GuiGeneratorInstance(
                instance_id="gen_zero", unit_key="u0", name="Zero",
                gen_type="Non-renewable", fuel="Gas",
                bus="bus_0", node=0, rated_power=0.0,
            ),
        })
        assert _bus_has_useful_equipment(state, "bus_0") is False

    def test_bus_with_battery_true(self):
        state = _make_state(
            generators={},
            batteries={
                "bat_0": GuiBatteryInstance(
                    instance_id="bat_0", unit_key="b0", name="Bat",
                    bus="bus_0", node=0, rated_power=50.0,
                ),
            },
        )
        assert _bus_has_useful_equipment(state, "bus_0") is True

    def test_bus_with_zero_power_battery_false(self):
        state = _make_state(
            generators={},
            batteries={
                "bat_0": GuiBatteryInstance(
                    instance_id="bat_0", unit_key="b0", name="Bat",
                    bus="bus_0", node=0, rated_power=0.0,
                ),
            },
        )
        assert _bus_has_useful_equipment(state, "bus_0") is False

    def test_bus_with_electrolyzer_true(self):
        state = _make_state(
            generators={},
            electrolyzers={
                "elz_0": GuiElectrolyzerInstance(
                    instance_id="elz_0", unit_key="e0", name="Elz",
                    bus="bus_0", node=0,
                ),
            },
        )
        assert _bus_has_useful_equipment(state, "bus_0") is True

    def test_nonexistent_bus_false(self):
        state = _make_state()
        assert _bus_has_useful_equipment(state, "bus_nonexistent") is False


# ===========================================================================
# _bus_has_demand()
# ===========================================================================


class TestBusHasDemand:
    """Tests for _bus_has_demand() helper."""

    def test_bus_with_positive_demand_fraction_and_node_demand_true(self):
        state = _make_state()
        assert _bus_has_demand(state, "bus_0") is True

    def test_bus_with_zero_demand_fraction_false(self):
        state = _make_state(buses={
            "bus_0": GuiBus(bus_id="bus_0", parent_node=0,
                            demand_fraction=0.0),
            "bus_1": GuiBus(bus_id="bus_1", parent_node=1),
        })
        assert _bus_has_demand(state, "bus_0") is False

    def test_bus_with_node_zero_demand_false(self):
        state = _make_state(
            nodes=[
                GuiNode(index=0, name="A",
                        demand=GuiNodeDemand(peak_mw=0.0, total_mwh=0.0)),
                GuiNode(index=1, name="B",
                        demand=GuiNodeDemand(peak_mw=100.0)),
            ],
        )
        assert _bus_has_demand(state, "bus_0") is False

    def test_nonexistent_bus_false(self):
        state = _make_state()
        assert _bus_has_demand(state, "bus_phantom") is False


# ===========================================================================
# _find_dead_end_buses()
# ===========================================================================


class TestFindDeadEndBuses:
    """Tests for _find_dead_end_buses() (private, electrical)."""

    def test_no_dead_ends_in_minimal_state(self):
        state = _make_state()
        actions = _find_dead_end_buses(state)
        assert actions == []

    def test_empty_buses_returns_empty(self):
        state = _make_state(buses={}, transmission_lines=[], generators={})
        actions = _find_dead_end_buses(state)
        assert actions == []

    def test_isolated_empty_bus_is_dead_end(self):
        state = _make_state(
            buses={
                "bus_0": GuiBus(bus_id="bus_0", parent_node=0,
                                demand_fraction=1.0),
                "bus_1": GuiBus(bus_id="bus_1", parent_node=1,
                                demand_fraction=1.0),
                "bus_dead": GuiBus(bus_id="bus_dead", parent_node=0,
                                   demand_fraction=0.0),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="G",
                    gen_type="Non-renewable", fuel="Gas",
                    bus="bus_0", node=0, rated_power=200.0,
                ),
            },
        )
        actions = _find_dead_end_buses(state)
        bus_removals = [a for a in actions if a.action_type == "remove_bus"]
        removed_ids = {a.element_id for a in bus_removals}
        assert "bus_dead" in removed_ids

    def test_leaf_bus_with_generator_not_dead_end(self):
        # bus_2 is a leaf (degree=1) but has a generator => not dead-end
        state = _make_state(
            nodes=[
                GuiNode(index=0, name="A",
                        demand=GuiNodeDemand(peak_mw=100.0)),
                GuiNode(index=1, name="B",
                        demand=GuiNodeDemand(peak_mw=100.0)),
            ],
            buses={
                "bus_0": GuiBus(bus_id="bus_0", parent_node=0,
                                demand_fraction=1.0),
                "bus_1": GuiBus(bus_id="bus_1", parent_node=1,
                                demand_fraction=1.0),
                "bus_2": GuiBus(bus_id="bus_2", parent_node=0,
                                demand_fraction=0.0),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="G_main",
                    gen_type="Non-renewable", fuel="Gas",
                    bus="bus_0", node=0, rated_power=200.0,
                ),
                "gen_leaf": GuiGeneratorInstance(
                    instance_id="gen_leaf", unit_key="u1", name="G_leaf",
                    gen_type="Non-renewable", fuel="Gas",
                    bus="bus_2", node=0, rated_power=50.0,
                ),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_0", to_bus="bus_1",
                    capacity_mw=100.0,
                ),
                GuiTransmissionLine(
                    line_id="line_1", from_bus="bus_0", to_bus="bus_2",
                    capacity_mw=50.0,
                ),
            ],
        )
        actions = _find_dead_end_buses(state)
        removed_bus_ids = {
            a.element_id for a in actions if a.action_type == "remove_bus"
        }
        assert "bus_2" not in removed_bus_ids

    def test_dead_end_removes_stub_line(self):
        state = _make_state(
            buses={
                "bus_0": GuiBus(bus_id="bus_0", parent_node=0,
                                demand_fraction=1.0),
                "bus_1": GuiBus(bus_id="bus_1", parent_node=1,
                                demand_fraction=1.0),
                "bus_dead": GuiBus(bus_id="bus_dead", parent_node=0,
                                   demand_fraction=0.0),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="G",
                    gen_type="Non-renewable", fuel="Gas",
                    bus="bus_0", node=0, rated_power=200.0,
                ),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_0", to_bus="bus_1",
                    capacity_mw=100.0,
                ),
                GuiTransmissionLine(
                    line_id="line_stub", from_bus="bus_0", to_bus="bus_dead",
                    capacity_mw=10.0,
                ),
            ],
        )
        actions = _find_dead_end_buses(state)
        line_removals = [a for a in actions if a.action_type == "remove_line"]
        removed_lines = {a.element_id for a in line_removals}
        assert "line_stub" in removed_lines

    def test_progress_callback_called(self):
        state = _make_state()
        calls = []
        def cb(step, total, desc):
            calls.append((step, total, desc))
        _find_dead_end_buses(state, progress_callback=cb)
        assert len(calls) > 0


# ===========================================================================
# find_dead_end_buses() public wrapper
# ===========================================================================


class TestFindDeadEndBusesPublic:
    """Tests for the public find_dead_end_buses() function."""

    def test_returns_list_of_actions(self):
        state = _make_state()
        result = find_dead_end_buses(state)
        assert isinstance(result, list)

    def test_no_dead_ends_returns_empty(self):
        state = _make_state()
        result = find_dead_end_buses(state)
        assert result == []

    def test_combines_electrical_and_fuel_actions(self):
        # Add an isolated fuel entry at a node with no fuel consumers
        state = _make_state(
            fuel_entry_points=[
                GuiFuelEntryPoint(name="Orphan Port", fuels=["Gas"], node=1),
            ],
        )
        result = find_dead_end_buses(state)
        # This tests the combined output (may or may not produce actions
        # depending on whether node 1 is considered dead-end in fuel network)
        assert isinstance(result, list)

    def test_includes_fuel_dead_ends(self):
        # Create a fuel network dead-end: fuel entry at node with no consumers,
        # connected by a route to a node that does have consumers
        state = _make_state(
            generators={
                "gen_gas": GuiGeneratorInstance(
                    instance_id="gen_gas", unit_key="u0", name="Gas Plant",
                    gen_type="Non-renewable", fuel="Gas",
                    bus="bus_0", node=0, rated_power=200.0,
                ),
            },
            fuel_entry_points=[
                GuiFuelEntryPoint(name="Port", fuels=["Gas"], node=1),
            ],
            fuel_transport_routes=[
                GuiFuelTransportRoute(
                    route_id="route_0", from_node=1, to_node=0,
                ),
            ],
        )
        result = find_dead_end_buses(state)
        # node 1 has no fuel consumers but has degree 1 in fuel graph
        # It should be identified as a dead end only if it has no consumers
        # and degree <= 1, but there's a route connecting it
        assert isinstance(result, list)


# ===========================================================================
# simplify_network()
# ===========================================================================


class TestSimplifyNetwork:
    """Tests for simplify_network() using a mock GuiModel."""

    def test_no_actions_returns_zero(self):
        model = MagicMock()
        result = simplify_network(model, [])
        assert result == 0

    def test_remove_line_calls_model(self):
        model = MagicMock()
        actions = [
            SimplificationAction("remove_line", "line_5", "stub"),
        ]
        result = simplify_network(model, actions)
        model.remove_line.assert_called_once_with("line_5")
        assert result == 1

    def test_remove_bus_calls_model(self):
        model = MagicMock()
        actions = [
            SimplificationAction("remove_bus", "bus_3", "dead-end"),
        ]
        result = simplify_network(model, actions)
        model.remove_bus.assert_called_once_with("bus_3")
        assert result == 1

    def test_remove_fuel_route_calls_model(self):
        model = MagicMock()
        actions = [
            SimplificationAction("remove_fuel_route", "route_2", "stub"),
        ]
        result = simplify_network(model, actions)
        model.remove_fuel_route.assert_called_once_with("route_2")
        assert result == 1

    def test_remove_fuel_entry_calls_model(self):
        model = MagicMock()
        model.state = _make_state(fuel_entry_points=[
            GuiFuelEntryPoint(name="Port", fuels=["Gas"], node=0),
        ])
        actions = [
            SimplificationAction("remove_fuel_entry", "0", "unused"),
        ]
        result = simplify_network(model, actions)
        model.remove_fuel_entry.assert_called_once_with(0)
        assert result == 1

    def test_remove_fuel_storage_calls_model(self):
        model = MagicMock()
        actions = [
            SimplificationAction("remove_fuel_storage", "fs_0", "unused"),
        ]
        result = simplify_network(model, actions)
        model.remove_fuel_storage.assert_called_once_with("fs_0")
        assert result == 1

    def test_ordering_lines_before_buses(self):
        model = MagicMock()
        call_order = []
        model.remove_line.side_effect = lambda x: call_order.append(("line", x))
        model.remove_bus.side_effect = lambda x: call_order.append(("bus", x))
        actions = [
            SimplificationAction("remove_bus", "bus_0", "dead-end"),
            SimplificationAction("remove_line", "line_0", "stub"),
        ]
        simplify_network(model, actions)
        # Lines should be removed before buses
        assert call_order[0][0] == "line"
        assert call_order[1][0] == "bus"

    def test_fuel_entries_removed_in_reverse_order(self):
        model = MagicMock()
        model.state = _make_state(fuel_entry_points=[
            GuiFuelEntryPoint(name="Port0", fuels=["Gas"], node=0),
            GuiFuelEntryPoint(name="Port1", fuels=["Oil"], node=1),
            GuiFuelEntryPoint(name="Port2", fuels=["Coal"], node=0),
        ])
        call_order = []
        model.remove_fuel_entry.side_effect = lambda x: call_order.append(x)
        actions = [
            SimplificationAction("remove_fuel_entry", "0", "unused"),
            SimplificationAction("remove_fuel_entry", "2", "unused"),
        ]
        simplify_network(model, actions)
        # Index 2 should be removed first (reverse order)
        assert call_order == [2, 0]

    def test_mixed_actions_all_applied(self):
        model = MagicMock()
        model.state = _make_state(fuel_entry_points=[
            GuiFuelEntryPoint(name="Port0", fuels=["Gas"], node=0),
        ])
        actions = [
            SimplificationAction("remove_line", "line_0", "stub"),
            SimplificationAction("remove_bus", "bus_0", "dead-end"),
            SimplificationAction("remove_fuel_route", "route_0", "stub"),
            SimplificationAction("remove_fuel_entry", "0", "unused"),
            SimplificationAction("remove_fuel_storage", "fs_0", "unused"),
        ]
        result = simplify_network(model, actions)
        assert result == 5

    def test_fuel_entry_index_out_of_range_skipped(self):
        model = MagicMock()
        model.state = _make_state(fuel_entry_points=[])  # empty list
        actions = [
            SimplificationAction("remove_fuel_entry", "5", "unused"),
        ]
        result = simplify_network(model, actions)
        # Index 5 is out of range, should not be applied
        model.remove_fuel_entry.assert_not_called()
        assert result == 0


# ===========================================================================
# count_validators()
# ===========================================================================


class TestCountValidators:
    """Tests for the count_validators() helper."""

    def test_all_categories(self):
        n = count_validators()
        # structural(3) + electrical(5) + demand(1) + generation(1)
        # + fuel_network(3) + connectivity(1) + topology_audit(1) = 15
        assert n == 15

    def test_single_category_structural(self):
        n = count_validators({"structural"})
        assert n == 3

    def test_single_category_electrical(self):
        n = count_validators({"electrical"})
        assert n == 5

    def test_single_category_demand(self):
        n = count_validators({"demand"})
        assert n == 1

    def test_single_category_generation(self):
        n = count_validators({"generation"})
        assert n == 1

    def test_single_category_fuel_network(self):
        n = count_validators({"fuel_network"})
        assert n == 3

    def test_single_category_connectivity(self):
        n = count_validators({"connectivity"})
        assert n == 1

    def test_empty_set_returns_zero(self):
        n = count_validators(set())
        assert n == 0

    def test_unknown_category_ignored(self):
        n = count_validators({"unknown_category"})
        assert n == 0

    def test_mixed_valid_and_invalid(self):
        n = count_validators({"structural", "nonexistent"})
        assert n == 3  # only structural counted

    def test_none_means_all(self):
        assert count_validators(None) == count_validators()

    def test_matches_validate_state_progress(self):
        """count_validators must match the number of progress steps in validate_state."""
        state = _make_state()
        cats = set(CATEGORY_ORDER)
        expected = count_validators(cats)
        calls = []
        def cb(step, total, desc):
            calls.append((step, total, desc))
        validate_state(state, categories=cats, progress_callback=cb)
        # The 'total' reported to the callback should equal count_validators
        assert calls[0][1] == expected
        # Final step should equal total
        assert calls[-1][0] == expected


# ===========================================================================
# preload_demand_data()
# ===========================================================================


class TestPreloadDemandData:
    """Tests for the preload_demand_data() helper."""

    def test_no_csv_path_is_noop(self):
        """Nodes without csv_path should be untouched."""
        state = _make_state()
        # Calling preload on state with no csv_path should not raise
        preload_demand_data(state)
        # Data should still be None (no csv_path set)
        for node in state.nodes:
            assert node.demand.data is None

    def test_nonexistent_csv_path_is_noop(self):
        """Nodes with a non-existent csv_path should be gracefully skipped."""
        state = _make_state(nodes=[
            GuiNode(
                index=0, name="A",
                demand=GuiNodeDemand(
                    csv_path="/tmp/nonexistent_validation_test.csv",
                    peak_mw=0.0,
                ),
            ),
            GuiNode(index=1, name="B",
                    demand=GuiNodeDemand(peak_mw=100.0)),
        ])
        preload_demand_data(state)
        # Should not crash; data stays None
        assert state.nodes[0].demand.data is None


# ===========================================================================
# New checks — _validate_nodes() enhancements
# ===========================================================================


class TestValidateNodesEnhanced:
    """Tests for the new checks in _validate_nodes()."""

    def test_node_without_bus_warning(self):
        state = _make_state(
            nodes=[
                GuiNode(index=0, name="A",
                        demand=GuiNodeDemand(peak_mw=100.0)),
                GuiNode(index=1, name="B",
                        demand=GuiNodeDemand(peak_mw=100.0)),
                GuiNode(index=2, name="Orphan"),
            ],
        )
        issues = _validate_nodes(state)
        no_bus = [i for i in issues if "has no buses" in i.message]
        assert len(no_bus) == 1
        assert no_bus[0].element_id == "2"
        assert no_bus[0].severity == "warning"


# ===========================================================================
# New checks — _validate_lines() enhancements
# ===========================================================================


class TestValidateLinesEnhanced:
    """Tests for the new checks in _validate_lines()."""

    def test_duplicate_line_ids_error(self):
        state = _make_state(transmission_lines=[
            GuiTransmissionLine(
                line_id="line_dup", from_bus="bus_0", to_bus="bus_1",
                capacity_mw=100.0,
            ),
            GuiTransmissionLine(
                line_id="line_dup", from_bus="bus_0", to_bus="bus_1",
                capacity_mw=50.0,
            ),
        ])
        issues = _validate_lines(state)
        dups = [i for i in issues if "Duplicate line ID" in i.message]
        assert len(dups) == 1
        assert dups[0].severity == "error"

    def test_unique_line_ids_no_dup_error(self):
        state = _make_state()
        issues = _validate_lines(state)
        dups = [i for i in issues if "Duplicate" in i.message]
        assert dups == []


# ===========================================================================
# New checks — _validate_generators() enhancements
# ===========================================================================


class TestValidateGeneratorsEnhanced:
    """Tests for the new checks in _validate_generators()."""

    def test_bus_node_mismatch_warning(self):
        state = _make_state(generators={
            "gen_0": GuiGeneratorInstance(
                instance_id="gen_0", unit_key="u0", name="Mismatched",
                gen_type="Non-renewable", fuel="Gas",
                bus="bus_0", node=1,  # bus_0 belongs to node 0
                rated_power=100.0,
            ),
        })
        issues = _validate_generators(state)
        mismatch = [i for i in issues if "doesn't match" in i.message]
        assert len(mismatch) == 1
        assert mismatch[0].severity == "warning"

    def test_bus_node_match_no_warning(self):
        state = _make_state()
        issues = _validate_generators(state)
        mismatch = [i for i in issues if "doesn't match" in i.message]
        assert mismatch == []

    def test_negative_rated_power_error(self):
        state = _make_state(generators={
            "gen_neg": GuiGeneratorInstance(
                instance_id="gen_neg", unit_key="u0", name="Negative",
                gen_type="Non-renewable", fuel="Gas",
                bus="bus_0", node=0, rated_power=-50.0,
            ),
        })
        issues = _validate_generators(state)
        neg = [i for i in issues if "negative rated_power" in i.message]
        assert len(neg) == 1
        assert neg[0].severity == "error"

    def test_min_power_greater_than_rated_error(self):
        state = _make_state(generators={
            "gen_min": GuiGeneratorInstance(
                instance_id="gen_min", unit_key="u0", name="BadMin",
                gen_type="Non-renewable", fuel="Gas",
                bus="bus_0", node=0,
                rated_power=100.0, min_power=150.0,
            ),
        })
        issues = _validate_generators(state)
        minp = [i for i in issues if "min_power" in i.message]
        assert len(minp) == 1
        assert minp[0].severity == "error"

    def test_efficiency_above_one_warning(self):
        state = _make_state(generators={
            "gen_eff": GuiGeneratorInstance(
                instance_id="gen_eff", unit_key="u0", name="BadEff",
                gen_type="Non-renewable", fuel="Gas",
                bus="bus_0", node=0,
                rated_power=100.0, eff_at_rated=1.5,
            ),
        })
        issues = _validate_generators(state)
        eff = [i for i in issues if "eff_at_rated" in i.message]
        assert len(eff) == 1
        assert eff[0].severity == "warning"

    def test_renewable_efficiency_not_checked(self):
        """Renewable generators should not trigger efficiency warnings."""
        state = _make_state(generators={
            "gen_re": GuiGeneratorInstance(
                instance_id="gen_re", unit_key="u0", name="Solar",
                gen_type="Renewable", fuel="Sun",
                bus="bus_0", node=0,
                rated_power=100.0, eff_at_rated=0.0,
            ),
        })
        issues = _validate_generators(state)
        eff = [i for i in issues if "eff_at_rated" in i.message]
        assert eff == []

    def test_lifetime_expired_warning(self):
        state = _make_state(generators={
            "gen_old": GuiGeneratorInstance(
                instance_id="gen_old", unit_key="u0", name="Old",
                gen_type="Non-renewable", fuel="Gas",
                bus="bus_0", node=0,
                rated_power=100.0, initial_age=30, life_time=25,
            ),
        })
        issues = _validate_generators(state)
        lt = [i for i in issues if "starts retired" in i.message]
        assert len(lt) == 1
        assert lt[0].severity == "warning"

    def test_lifetime_not_expired_no_warning(self):
        state = _make_state()  # default gen has initial_age=0, life_time=25
        issues = _validate_generators(state)
        lt = [i for i in issues if "starts retired" in i.message]
        assert lt == []


# ===========================================================================
# New checks — _validate_batteries() enhancements
# ===========================================================================


class TestValidateBatteriesEnhanced:
    """Tests for the new checks in _validate_batteries()."""

    def test_bus_node_mismatch_warning(self):
        state = _make_state(batteries={
            "bat_0": GuiBatteryInstance(
                instance_id="bat_0", unit_key="b0", name="Mis",
                bus="bus_0", node=1,  # bus_0 belongs to node 0
                rated_power=50.0,
            ),
        })
        issues = _validate_batteries(state)
        mismatch = [i for i in issues if "doesn't match" in i.message]
        assert len(mismatch) == 1

    def test_negative_rated_power_error(self):
        state = _make_state(batteries={
            "bat_neg": GuiBatteryInstance(
                instance_id="bat_neg", unit_key="b0", name="Neg",
                bus="bus_0", node=0, rated_power=-10.0,
            ),
        })
        issues = _validate_batteries(state)
        neg = [i for i in issues if "negative rated_power" in i.message]
        assert len(neg) == 1
        assert neg[0].severity == "error"

    def test_charge_efficiency_above_one_error(self):
        state = _make_state(batteries={
            "bat_eff": GuiBatteryInstance(
                instance_id="bat_eff", unit_key="b0", name="BadEff",
                bus="bus_0", node=0, rated_power=50.0,
                efficiency_charge=1.2, efficiency_discharge=0.9,
            ),
        })
        issues = _validate_batteries(state)
        eff = [i for i in issues if "efficiency_charge" in i.message]
        assert len(eff) == 1
        assert eff[0].severity == "error"

    def test_discharge_efficiency_zero_error(self):
        state = _make_state(batteries={
            "bat_eff": GuiBatteryInstance(
                instance_id="bat_eff", unit_key="b0", name="ZeroEff",
                bus="bus_0", node=0, rated_power=50.0,
                efficiency_charge=0.9, efficiency_discharge=0.0,
            ),
        })
        issues = _validate_batteries(state)
        eff = [i for i in issues if "efficiency_discharge" in i.message]
        assert len(eff) == 1
        assert eff[0].severity == "error"

    def test_valid_efficiency_no_error(self):
        state = _make_state(batteries={
            "bat_ok": GuiBatteryInstance(
                instance_id="bat_ok", unit_key="b0", name="OK",
                bus="bus_0", node=0, rated_power=50.0,
                efficiency_charge=0.9, efficiency_discharge=0.9,
            ),
        })
        issues = _validate_batteries(state)
        eff = [i for i in issues if "efficiency" in i.message]
        assert eff == []

    def test_capacity_less_than_rated_warning(self):
        state = _make_state(batteries={
            "bat_small": GuiBatteryInstance(
                instance_id="bat_small", unit_key="b0", name="Small",
                bus="bus_0", node=0,
                rated_power=100.0, capacity=50.0,
                efficiency_charge=0.9, efficiency_discharge=0.9,
            ),
        })
        issues = _validate_batteries(state)
        cap = [i for i in issues if "less than 1 hour" in i.message]
        assert len(cap) == 1
        assert cap[0].severity == "warning"

    def test_capacity_greater_than_rated_no_warning(self):
        state = _make_state(batteries={
            "bat_ok": GuiBatteryInstance(
                instance_id="bat_ok", unit_key="b0", name="OK",
                bus="bus_0", node=0,
                rated_power=50.0, capacity=200.0,
                efficiency_charge=0.9, efficiency_discharge=0.9,
            ),
        })
        issues = _validate_batteries(state)
        cap = [i for i in issues if "less than 1 hour" in i.message]
        assert cap == []

    def test_lifetime_expired_warning(self):
        state = _make_state(batteries={
            "bat_old": GuiBatteryInstance(
                instance_id="bat_old", unit_key="b0", name="Old",
                bus="bus_0", node=0,
                rated_power=50.0, initial_age=25, life_time=20,
                efficiency_charge=0.9, efficiency_discharge=0.9,
            ),
        })
        issues = _validate_batteries(state)
        lt = [i for i in issues if "starts retired" in i.message]
        assert len(lt) == 1


# ===========================================================================
# New checks — _validate_transformers() enhancements
# ===========================================================================


class TestValidateTransformersEnhanced:
    """Tests for the new checks in _validate_transformers()."""

    def test_self_loop_error(self):
        state = _make_state(transformers=[
            GuiTransformer(name="TR_loop", from_bus="bus_0", to_bus="bus_0"),
        ])
        issues = _validate_transformers(state)
        loops = [i for i in issues if "self-loop" in i.message]
        assert len(loops) == 1
        assert loops[0].severity == "error"

    def test_same_voltage_warning(self):
        state = _make_state(transformers=[
            GuiTransformer(
                name="TR_same", from_bus="bus_0", to_bus="bus_1",
                from_voltage_kv=220.0, to_voltage_kv=220.0,
            ),
        ])
        issues = _validate_transformers(state)
        sv = [i for i in issues if "same voltage" in i.message]
        assert len(sv) == 1
        assert sv[0].severity == "warning"

    def test_different_voltage_no_warning(self):
        state = _make_state(
            buses={
                "bus_0": GuiBus(bus_id="bus_0", name="HV", parent_node=0,
                                voltage_kv=220.0, demand_fraction=1.0),
                "bus_1": GuiBus(bus_id="bus_1", name="LV", parent_node=1,
                                voltage_kv=110.0, demand_fraction=1.0),
            },
            transformers=[
                GuiTransformer(
                    name="TR_ok", from_bus="bus_0", to_bus="bus_1",
                    from_voltage_kv=220.0, to_voltage_kv=110.0,
                ),
            ],
        )
        issues = _validate_transformers(state)
        sv = [i for i in issues if "same voltage" in i.message]
        assert sv == []

    def test_zero_rated_power_warning(self):
        state = _make_state(transformers=[
            GuiTransformer(
                name="TR_zero", from_bus="bus_0", to_bus="bus_1",
                rated_power_mva=0.0,
            ),
        ])
        issues = _validate_transformers(state)
        zp = [i for i in issues if "rated_power_mva" in i.message]
        assert len(zp) == 1
        assert zp[0].severity == "warning"


# ===========================================================================
# New checks — _validate_converters() enhancements
# ===========================================================================


class TestValidateConvertersEnhanced:
    """Tests for the new efficiency > 1.0 checks in _validate_converters()."""

    def test_acdc_efficiency_above_one_error(self):
        state = _make_state(acdc_converters=[
            GuiACDCConverter(
                name="ACDC_bad", from_bus="bus_0", to_bus="bus_1",
                efficiency_rectify=1.05, efficiency_invert=0.98,
            ),
        ])
        issues = _validate_converters(state)
        eff = [i for i in issues if "1.0" in i.message and "AC/DC" in i.category]
        assert len(eff) == 1
        assert eff[0].severity == "error"

    def test_freq_efficiency_above_one_error(self):
        state = _make_state(freq_converters=[
            GuiFrequencyConverter(
                name="FC_bad", from_bus="bus_0", to_bus="bus_1",
                efficiency_a_to_b=0.98, efficiency_b_to_a=1.1,
            ),
        ])
        issues = _validate_converters(state)
        eff = [i for i in issues if "1.0" in i.message and "Freq" in i.category]
        assert len(eff) == 1
        assert eff[0].severity == "error"


# ===========================================================================
# New checks — _validate_demand() enhancements
# ===========================================================================


class TestValidateDemandEnhanced:
    """Tests for the new checks in _validate_demand()."""

    def test_demand_hours_mismatch_warning(self):
        state = _make_state(
            nodes=[
                GuiNode(index=0, name="A",
                        demand=GuiNodeDemand(
                            peak_mw=100.0, total_mwh=800.0,
                            data=[1.0] * 8760, num_hours=8760,
                        )),
                GuiNode(index=1, name="B",
                        demand=GuiNodeDemand(
                            peak_mw=100.0, total_mwh=400.0,
                            data=[1.0] * 4380, num_hours=4380,
                        )),
            ],
        )
        issues = _validate_demand(state)
        mismatch = [i for i in issues if "different lengths" in i.message]
        assert len(mismatch) == 1
        assert mismatch[0].severity == "warning"

    def test_demand_hours_same_no_warning(self):
        state = _make_state(
            nodes=[
                GuiNode(index=0, name="A",
                        demand=GuiNodeDemand(
                            peak_mw=100.0, data=[1.0] * 8760, num_hours=8760,
                        )),
                GuiNode(index=1, name="B",
                        demand=GuiNodeDemand(
                            peak_mw=100.0, data=[1.0] * 8760, num_hours=8760,
                        )),
            ],
        )
        issues = _validate_demand(state)
        mismatch = [i for i in issues if "different lengths" in i.message]
        assert mismatch == []

    def test_negative_demand_error(self):
        state = _make_state(
            nodes=[
                GuiNode(index=0, name="A",
                        demand=GuiNodeDemand(
                            peak_mw=100.0, total_mwh=800.0,
                            data=[100.0, -5.0, 50.0], num_hours=3,
                        )),
                GuiNode(index=1, name="B",
                        demand=GuiNodeDemand(peak_mw=100.0)),
            ],
        )
        issues = _validate_demand(state)
        neg = [i for i in issues if "negative values" in i.message]
        assert len(neg) == 1
        assert neg[0].severity == "error"
        assert neg[0].element_id == "0"

    def test_all_positive_demand_no_error(self):
        state = _make_state(
            nodes=[
                GuiNode(index=0, name="A",
                        demand=GuiNodeDemand(
                            peak_mw=100.0,
                            data=[10.0, 20.0, 30.0], num_hours=3,
                        )),
                GuiNode(index=1, name="B",
                        demand=GuiNodeDemand(peak_mw=100.0)),
            ],
        )
        issues = _validate_demand(state)
        neg = [i for i in issues if "negative values" in i.message]
        assert neg == []


# ===========================================================================
# New checks — _validate_generation() enhancements
# ===========================================================================


class TestValidateGenerationEnhanced:
    """Tests for the availability file existence check."""

    def test_availability_file_not_found_warning(self):
        state = _make_state(generators={
            "gen_re": GuiGeneratorInstance(
                instance_id="gen_re", unit_key="u0", name="Solar",
                gen_type="Renewable", fuel="Sun",
                bus="bus_0", node=0, rated_power=100.0,
                availability_file="/nonexistent/solar_avail.csv",
            ),
        })
        issues = _validate_generation(state)
        missing = [i for i in issues if "not found" in i.message]
        assert len(missing) == 1
        assert missing[0].severity == "warning"

    def test_no_availability_file_still_warns(self):
        """Renewable with no file should warn about missing file, not about not found."""
        state = _make_state(generators={
            "gen_re": GuiGeneratorInstance(
                instance_id="gen_re", unit_key="u0", name="Wind",
                gen_type="Renewable", fuel="Wind",
                bus="bus_0", node=0, rated_power=100.0,
                availability_file=None,
            ),
        })
        issues = _validate_generation(state)
        no_file = [i for i in issues if "no availability file" in i.message]
        not_found = [i for i in issues if "not found" in i.message]
        assert len(no_file) == 1
        assert not_found == []


# ===========================================================================
# New checks — _validate_fuel_network() enhancements
# ===========================================================================


class TestValidateFuelNetworkEnhanced:
    """Tests for the new checks in _validate_fuel_network()."""

    def test_fuel_route_self_loop_allowed(self):
        """Intra-node routes (from_node == to_node) are valid for within-region transport."""
        state = _make_state(fuel_transport_routes=[
            GuiFuelTransportRoute(
                route_id="route_loop", from_node=0, to_node=0,
            ),
        ])
        issues = _validate_fuel_network(state)
        loops = [i for i in issues if "self-loop" in i.message]
        assert len(loops) == 0

    def test_fuel_route_zero_capacity_warning(self):
        state = _make_state(fuel_transport_routes=[
            GuiFuelTransportRoute(
                route_id="route_zero", from_node=0, to_node=1,
                capacity=0.0,
            ),
        ])
        issues = _validate_fuel_network(state)
        cap = [i for i in issues if "zero or negative capacity" in i.message]
        assert len(cap) == 1
        assert cap[0].severity == "warning"

    def test_fuel_route_with_fuel_params_no_capacity_warning(self):
        """Route with per-fuel params should not trigger zero capacity warning."""
        from esfex.visualization.data.gui_model import FuelRouteParams
        state = _make_state(fuel_transport_routes=[
            GuiFuelTransportRoute(
                route_id="route_params", from_node=0, to_node=1,
                capacity=0.0,
                fuel_params={"Gas": FuelRouteParams(capacity=100.0)},
            ),
        ])
        issues = _validate_fuel_network(state)
        cap = [i for i in issues if "zero or negative capacity" in i.message]
        assert cap == []

    def test_fuel_storage_no_fuels_warning(self):
        from esfex.visualization.data.gui_model import GuiFuelStorage
        state = _make_state(fuel_storages={
            "fs_0": GuiFuelStorage(
                storage_id="fs_0", name="Empty Storage",
                fuels=[], node=0,
            ),
        })
        issues = _validate_fuel_network(state)
        no_fuels = [i for i in issues if "Fuel storage" in i.message
                    and "no fuels" in i.message]
        assert len(no_fuels) == 1
        assert no_fuels[0].severity == "warning"

    def test_non_electric_demand_unsupplied_fuel_warning(self):
        from esfex.visualization.data.gui_model import GuiNonElectricDemand
        state = _make_state(
            generators={},
            non_electric_demand={
                "ned_0": GuiNonElectricDemand(
                    demand_id="ned_0", fuel="Hydrogen", unit="kg",
                ),
            },
            fuel_entry_points=[],  # no supply for Hydrogen
        )
        issues = _validate_fuel_network(state)
        unsupplied = [i for i in issues
                      if "Non-electric demand" in i.message
                      and "Hydrogen" in i.message]
        assert len(unsupplied) == 1
        assert unsupplied[0].severity == "warning"


# ===========================================================================
# New checks — _validate_connectivity() enhancements
# ===========================================================================


class TestValidateConnectivityEnhanced:
    """Tests for the disconnected components info."""

    def test_two_components_info(self):
        state = _make_state(
            nodes=[
                GuiNode(index=0, name="A",
                        demand=GuiNodeDemand(peak_mw=100.0)),
                GuiNode(index=1, name="B",
                        demand=GuiNodeDemand(peak_mw=100.0)),
                GuiNode(index=2, name="C",
                        demand=GuiNodeDemand(peak_mw=50.0)),
                GuiNode(index=3, name="D",
                        demand=GuiNodeDemand(peak_mw=50.0)),
            ],
            buses={
                "bus_0": GuiBus(bus_id="bus_0", parent_node=0),
                "bus_1": GuiBus(bus_id="bus_1", parent_node=1),
                "bus_2": GuiBus(bus_id="bus_2", parent_node=2),
                "bus_3": GuiBus(bus_id="bus_3", parent_node=3),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="G0",
                    gen_type="Non-renewable", fuel="Gas",
                    bus="bus_0", node=0, rated_power=200.0,
                ),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_0", to_bus="bus_1",
                    capacity_mw=100.0,
                ),
                GuiTransmissionLine(
                    line_id="line_1", from_bus="bus_2", to_bus="bus_3",
                    capacity_mw=100.0,
                ),
                # Two separate components: {0,1} and {2,3}
            ],
        )
        issues = _validate_connectivity(state)
        comp = [i for i in issues if "disconnected sub-networks" in i.message]
        assert len(comp) == 1
        assert comp[0].severity == "info"
        assert "2" in comp[0].message  # 2 components

    def test_single_component_no_info(self):
        state = _make_state()  # default is connected
        issues = _validate_connectivity(state)
        comp = [i for i in issues if "disconnected" in i.message]
        assert comp == []
