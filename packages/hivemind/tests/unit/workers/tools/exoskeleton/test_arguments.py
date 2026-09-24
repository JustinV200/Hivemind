"""Unit tests for hivemind.workers.tools.exoskeleton.arguments: shared schemas and parsers.

Fits into the Hive:
    Mirrors src/hivemind/workers/tools/exoskeleton/arguments.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.tools.exoskeleton.arguments for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import JsonValue

from hivemind.llm import JsonObject
from hivemind.workers.tools.exoskeleton import GuiArgumentError
from hivemind.workers.tools.exoskeleton.arguments import (
    EXPECT_SCHEMA,
    action_definition,
    build_step,
    flag,
    read_definition,
    required_int,
    target_from,
)
from waggle.messages.capping import ElementTarget, GuiOp

_SECRET = "hunter2-hunter2"  # noqa: S105 -- a probe value, never a real credential.


def test_an_action_definition_adds_expect_and_irreversible_and_closes_the_schema() -> None:
    definition = action_definition("click", "d", {"x": {"type": "integer"}}, ("x",))

    parameters = definition.parameters
    properties = parameters["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {"x", "expect", "irreversible"}
    assert properties["expect"] == EXPECT_SCHEMA
    assert parameters["required"] == ["x"]
    assert parameters["additionalProperties"] is False


def test_a_read_definition_carries_neither_expect_nor_irreversible() -> None:
    parameters = read_definition("see", "d", {"region": {"type": "string"}}).parameters

    assert parameters["properties"] == {"region": {"type": "string"}}
    assert parameters["required"] == []


def test_build_step_reports_a_broken_rule_without_quoting_the_typed_text() -> None:
    with pytest.raises(GuiArgumentError) as raised:
        build_step({"op": GuiOp.TYPE, "text": _SECRET * 400, "secret": True})

    assert "text" in str(raised.value) and "at most" in str(raised.value)
    assert _SECRET not in str(raised.value)
    assert raised.value.__cause__ is None  # The cause kept the input; it is dropped.


def test_build_step_refuses_a_field_the_op_does_not_take() -> None:
    with pytest.raises(GuiArgumentError, match="not allowed"):
        build_step({"op": GuiOp.PRESS, "keys": "Return", "text": "x"})


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ("role=button", "must be an object"),
        ({"role": "button", "text": "Log in"}, "exactly one way"),
        ({"label": "Username", "name": "Log in"}, "set role too"),
        ({"role": "button", "colour": "red"}, "colour"),
    ],
)
def test_target_from_refuses_anything_but_one_element_named_one_way(
    value: JsonValue, fragment: str
) -> None:
    with pytest.raises(GuiArgumentError) as raised:
        target_from(value)

    assert fragment in str(raised.value)


def test_target_from_accepts_a_role_with_its_accessible_name() -> None:
    assert target_from({"role": "link", "name": "Help"}) == ElementTarget(role="link", name="Help")


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        ({"x": True}, "x must be an integer"),
        ({"x": "5"}, "x must be an integer"),
        ({}, "x is required"),
    ],
)
def test_required_int_refuses_a_bool_a_string_or_nothing(
    arguments: JsonObject, fragment: str
) -> None:
    with pytest.raises(GuiArgumentError, match=fragment):
        required_int(arguments, "x")


def test_flag_reads_absent_as_false_and_refuses_a_non_boolean() -> None:
    assert flag({}, "secret") is False
    assert flag({"secret": True}, "secret") is True
    with pytest.raises(GuiArgumentError, match="true or false"):
        flag({"secret": "yes"}, "secret")
