"""Connection rules and validation for electrical element connectivity."""

from __future__ import annotations

# Element connection compatibility matrix
# Maps element_type -> set of allowed connection targets
CONNECTION_RULES: dict[str, set[str]] = {
    "generator": {"bus", "acdc_converter", "transformer"},
    "battery": {"bus", "acdc_converter", "transformer"},
    "electrolyzer": {"bus", "acdc_converter", "transformer"},
    "bus": {
        "generator",
        "battery",
        "electrolyzer",
        "transformer",
        "acdc_converter",
        "freq_converter",
        "bus",  # Bus-to-bus connections allowed
    },
    "transformer": {
        "generator",
        "battery",
        "electrolyzer",
        "bus",
        "transformer",  # Transformer-to-transformer cascading
        "acdc_converter",
        "freq_converter",
    },
    "acdc_converter": {
        "generator",
        "battery",
        "electrolyzer",
        "bus",
        "transformer",
        "acdc_converter",  # Converter-to-converter allowed
        "freq_converter",
    },
    "freq_converter": {
        "generator",
        "battery",
        "electrolyzer",
        "bus",
        "transformer",
        "acdc_converter",
        "freq_converter",
    },
    # Legacy: nodes can only connect to other nodes
    "node": {"node"},
}


def is_valid_connection(from_type: str, to_type: str) -> bool:
    """Check if two element types can be connected via a transmission line.

    Args:
        from_type: Element type at line start (e.g., "generator", "bus")
        to_type: Element type at line end

    Returns:
        True if connection is allowed, False otherwise

    Examples:
        >>> is_valid_connection("generator", "bus")
        True
        >>> is_valid_connection("generator", "node")
        False
        >>> is_valid_connection("transformer", "transformer")
        True
    """
    if from_type not in CONNECTION_RULES:
        return False
    return to_type in CONNECTION_RULES[from_type]


def get_connection_error_message(from_type: str, to_type: str) -> str:
    """Generate user-friendly error message for invalid connections.

    Args:
        from_type: Element type at line start
        to_type: Element type at line end

    Returns:
        Error message explaining why connection is invalid and listing
        valid connection targets

    Example:
        >>> msg = get_connection_error_message("generator", "node")
        >>> print(msg)
        Cannot connect generator to node.

        Generator can connect to: acdc_converter, bus, transformer
    """
    valid_targets = CONNECTION_RULES.get(from_type, set())
    if not valid_targets:
        return f"Cannot connect {from_type} to {to_type}.\n\n{from_type.capitalize()} cannot be connected via transmission lines."

    valid_str = ", ".join(sorted(valid_targets))
    return (
        f"Cannot connect {from_type} to {to_type}.\n\n"
        f"{from_type.capitalize()} can connect to: {valid_str}"
    )


def get_valid_connections(element_type: str) -> set[str]:
    """Get the set of valid connection targets for an element type.

    Args:
        element_type: Element type to query (e.g., "generator")

    Returns:
        Set of element types that can be connected to

    Example:
        >>> get_valid_connections("generator")
        {'bus', 'acdc_converter', 'transformer'}
    """
    return CONNECTION_RULES.get(element_type, set()).copy()
