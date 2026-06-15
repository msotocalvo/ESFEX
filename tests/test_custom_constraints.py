"""Tests for declarative user-defined optimization constraints (schema + adapter
name resolution). The Julia hook itself is covered by the Julia test suite."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from esfex.config.schema import (
    ConstraintTerm,
    CustomConstraintConfig,
    SystemConfig,
)


def _cc(**kw):
    base = dict(
        name="cap_gen", sense="<=", rhs=80.0,
        terms=[ConstraintTerm(variable="gen_output", index=["GasCC", "all"])],
    )
    base.update(kw)
    return CustomConstraintConfig(**base)


# ── Schema validation ──────────────────────────────────────────────────────

def test_linear_requires_terms():
    with pytest.raises(ValueError):
        CustomConstraintConfig(name="bad", type="linear", terms=[])


def test_defaults_and_fields():
    c = _cc()
    assert c.type == "linear" and c.target == "operational"
    assert c.terms[0].coefficient == 1.0
    assert c.sense == "<="


def test_invalid_sense_rejected():
    with pytest.raises(ValueError):
        _cc(sense="<")


def test_plugin_typed_needs_no_terms():
    c = CustomConstraintConfig(name="p", type="my_cap", terms=[],
                               params={"limit": 5})
    assert c.type == "my_cap" and c.params["limit"] == 5


# ── Adapter name → 1-based index resolution (pure, no Julia) ────────────────

def _sys(constraints):
    # Only the .keys() of generators/batteries matter for resolution.
    return SimpleNamespace(
        generators={"GasCC": object(), "Coal": object()},
        batteries={"Bat1": object()},
        custom_constraints=constraints,
    )


def test_resolve_generator_name_and_all():
    from esfex.bridge.adapters import resolve_custom_constraints
    specs = resolve_custom_constraints(_sys([_cc()]))
    assert len(specs) == 1
    term = specs[0]["terms"][0]
    assert term["index"] == [1, -1]          # GasCC → 1, "all" → -1
    assert specs[0]["sense"] == "<=" and specs[0]["rhs"] == 80.0


def test_resolve_second_generator_and_hour():
    from esfex.bridge.adapters import resolve_custom_constraints
    c = _cc(terms=[ConstraintTerm(variable="gen_output", index=["Coal", 3])])
    specs = resolve_custom_constraints(_sys([c]))
    assert specs[0]["terms"][0]["index"] == [2, 3]   # Coal → 2, hour 3 kept


def test_resolve_battery_name():
    from esfex.bridge.adapters import resolve_custom_constraints
    c = _cc(terms=[ConstraintTerm(variable="bat_soc", index=["Bat1", "all"])])
    specs = resolve_custom_constraints(_sys([c]))
    assert specs[0]["terms"][0]["index"] == [1, -1]


def test_unknown_generator_raises():
    from esfex.bridge.adapters import resolve_custom_constraints
    c = _cc(terms=[ConstraintTerm(variable="gen_output", index=["Ghost", "all"])])
    with pytest.raises(ValueError, match="unknown generator 'Ghost'"):
        resolve_custom_constraints(_sys([c]))


def test_no_constraints_returns_empty():
    from esfex.bridge.adapters import resolve_custom_constraints
    assert resolve_custom_constraints(_sys([])) == []


# ── Round-trip through the Pydantic model (YAML/.esfexp safe) ───────────────

def test_model_dump_round_trip():
    c = _cc()
    dumped = c.model_dump()
    again = CustomConstraintConfig(**dumped)
    assert again.terms[0].index == ["GasCC", "all"]
    assert again == c


def test_system_config_carries_constraints():
    # custom_constraints rides on SystemConfig and survives model_dump/reload.
    from esfex.config.schema import NodeConfig
    sys = SystemConfig(
        name="s", nodes=NodeConfig(nodes_connections=[]),
        custom_constraints=[_cc()],
    )
    d = sys.model_dump()
    assert d["custom_constraints"][0]["name"] == "cap_gen"
    assert SystemConfig(**d).custom_constraints[0].rhs == 80.0
