"""Tests for connectivity_rules module."""

from __future__ import annotations

import pytest

from esfex.visualization.data.connectivity_rules import (
    CONNECTION_RULES,
    get_connection_error_message,
    get_valid_connections,
    is_valid_connection,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_ELEMENT_TYPES = list(CONNECTION_RULES.keys())


def _all_valid_pairs():
    """Yield every (from_type, to_type) pair that should be valid."""
    for from_type, targets in CONNECTION_RULES.items():
        for to_type in targets:
            yield from_type, to_type


# ===========================================================================
# is_valid_connection
# ===========================================================================


class TestIsValidConnection:
    """Tests for is_valid_connection()."""

    # -- valid pairs --------------------------------------------------------

    @pytest.mark.parametrize("from_type, to_type", list(_all_valid_pairs()))
    def test_all_valid_pairs_return_true(self, from_type: str, to_type: str):
        assert is_valid_connection(from_type, to_type) is True

    # -- specific well-known valid cases ------------------------------------

    def test_generator_to_bus(self):
        assert is_valid_connection("generator", "bus") is True

    def test_generator_to_transformer(self):
        assert is_valid_connection("generator", "transformer") is True

    def test_generator_to_acdc_converter(self):
        assert is_valid_connection("generator", "acdc_converter") is True

    def test_battery_to_bus(self):
        assert is_valid_connection("battery", "bus") is True

    def test_bus_to_bus(self):
        assert is_valid_connection("bus", "bus") is True

    def test_transformer_to_transformer(self):
        assert is_valid_connection("transformer", "transformer") is True

    def test_acdc_to_acdc(self):
        assert is_valid_connection("acdc_converter", "acdc_converter") is True

    def test_freq_to_freq(self):
        assert is_valid_connection("freq_converter", "freq_converter") is True

    def test_node_to_node(self):
        assert is_valid_connection("node", "node") is True

    def test_electrolyzer_to_bus(self):
        assert is_valid_connection("electrolyzer", "bus") is True

    # -- invalid pairs ------------------------------------------------------

    def test_generator_to_node_invalid(self):
        assert is_valid_connection("generator", "node") is False

    def test_battery_to_node_invalid(self):
        assert is_valid_connection("battery", "node") is False

    def test_node_to_bus_invalid(self):
        assert is_valid_connection("node", "bus") is False

    def test_node_to_generator_invalid(self):
        assert is_valid_connection("node", "generator") is False

    def test_generator_to_generator_invalid(self):
        assert is_valid_connection("generator", "generator") is False

    def test_battery_to_battery_invalid(self):
        assert is_valid_connection("battery", "battery") is False

    def test_generator_to_battery_invalid(self):
        assert is_valid_connection("generator", "battery") is False

    def test_electrolyzer_to_node_invalid(self):
        assert is_valid_connection("electrolyzer", "node") is False

    # -- unknown types ------------------------------------------------------

    def test_unknown_from_type_returns_false(self):
        assert is_valid_connection("unknown_element", "bus") is False

    def test_unknown_to_type_returns_false(self):
        assert is_valid_connection("bus", "unknown_element") is False

    def test_both_unknown_returns_false(self):
        assert is_valid_connection("foo", "bar") is False

    def test_empty_string_from_type(self):
        assert is_valid_connection("", "bus") is False

    def test_empty_string_to_type(self):
        assert is_valid_connection("bus", "") is False


# ===========================================================================
# get_connection_error_message
# ===========================================================================


class TestGetConnectionErrorMessage:
    """Tests for get_connection_error_message()."""

    def test_invalid_pair_contains_both_types(self):
        msg = get_connection_error_message("generator", "node")
        assert "generator" in msg
        assert "node" in msg

    def test_invalid_pair_mentions_cannot_connect(self):
        msg = get_connection_error_message("generator", "node")
        assert "Cannot connect" in msg

    def test_invalid_pair_lists_valid_targets(self):
        msg = get_connection_error_message("generator", "node")
        # generator can connect to: acdc_converter, bus, transformer
        assert "bus" in msg
        assert "transformer" in msg
        assert "acdc_converter" in msg

    def test_valid_targets_are_sorted(self):
        msg = get_connection_error_message("generator", "node")
        # The sorted list should appear after "can connect to:"
        assert "acdc_converter, bus, transformer" in msg

    def test_unknown_from_type_message(self):
        msg = get_connection_error_message("unknown_thing", "bus")
        assert "Cannot connect" in msg
        assert "unknown_thing" in msg
        assert "cannot be connected via transmission lines" in msg

    def test_unknown_from_type_no_valid_targets(self):
        msg = get_connection_error_message("nonexistent", "bus")
        assert "can connect to:" not in msg

    @pytest.mark.parametrize("element_type", ALL_ELEMENT_TYPES)
    def test_known_type_message_contains_can_connect_to(self, element_type: str):
        """For every known type, the error message should list valid targets."""
        msg = get_connection_error_message(element_type, "INVALID_TARGET")
        if CONNECTION_RULES[element_type]:
            assert "can connect to:" in msg
        # node -> {"node"} so it should still have the message
        assert "Cannot connect" in msg

    def test_message_capitalizes_from_type(self):
        msg = get_connection_error_message("battery", "node")
        assert "Battery can connect to:" in msg

    def test_message_for_bus_lists_many_targets(self):
        msg = get_connection_error_message("bus", "nonexistent")
        # bus has the most targets
        for target in CONNECTION_RULES["bus"]:
            assert target in msg


# ===========================================================================
# get_valid_connections
# ===========================================================================


class TestGetValidConnections:
    """Tests for get_valid_connections()."""

    @pytest.mark.parametrize("element_type", ALL_ELEMENT_TYPES)
    def test_returns_correct_set_for_known_type(self, element_type: str):
        result = get_valid_connections(element_type)
        assert result == CONNECTION_RULES[element_type]

    def test_unknown_type_returns_empty_set(self):
        result = get_valid_connections("nonexistent")
        assert result == set()
        assert isinstance(result, set)

    def test_empty_string_returns_empty_set(self):
        result = get_valid_connections("")
        assert result == set()

    def test_returns_copy_not_original(self):
        """Mutating returned set must not affect the original rules."""
        original = CONNECTION_RULES["generator"].copy()
        result = get_valid_connections("generator")
        result.add("INJECTED")
        assert "INJECTED" not in CONNECTION_RULES["generator"]
        assert CONNECTION_RULES["generator"] == original

    def test_generator_has_three_targets(self):
        result = get_valid_connections("generator")
        assert result == {"bus", "acdc_converter", "transformer"}

    def test_battery_targets_same_as_generator(self):
        gen = get_valid_connections("generator")
        bat = get_valid_connections("battery")
        assert gen == bat

    def test_electrolyzer_targets_same_as_generator(self):
        gen = get_valid_connections("generator")
        elec = get_valid_connections("electrolyzer")
        assert gen == elec

    def test_node_only_connects_to_node(self):
        result = get_valid_connections("node")
        assert result == {"node"}

    def test_bus_includes_bus_to_bus(self):
        result = get_valid_connections("bus")
        assert "bus" in result

    def test_bus_includes_generators_and_batteries(self):
        result = get_valid_connections("bus")
        assert "generator" in result
        assert "battery" in result
        assert "electrolyzer" in result


# ===========================================================================
# CONNECTION_RULES structure
# ===========================================================================


class TestConnectionRulesStructure:
    """Sanity checks on the CONNECTION_RULES constant itself."""

    def test_is_dict(self):
        assert isinstance(CONNECTION_RULES, dict)

    def test_all_values_are_sets(self):
        for key, val in CONNECTION_RULES.items():
            assert isinstance(val, set), f"Value for {key!r} is not a set"

    def test_has_all_expected_element_types(self):
        expected = {
            "generator",
            "battery",
            "electrolyzer",
            "bus",
            "transformer",
            "acdc_converter",
            "freq_converter",
            "node",
        }
        assert expected == set(CONNECTION_RULES.keys())

    def test_no_empty_target_sets(self):
        for key, val in CONNECTION_RULES.items():
            assert len(val) > 0, f"Empty target set for {key!r}"
