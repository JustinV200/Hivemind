"""Tests for hivemind.royal_jelly.spec.validate: every refusal validate_spec makes.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/spec/validate.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.spec.validate for the module under test.
"""

from __future__ import annotations

import json

import pytest
from builders.royal_jelly import DEFAULT_SCHEMA, make_tool_spec

from hivemind.guard import CapabilitySet
from hivemind.royal_jelly.spec import ToolSpec, validate_spec
from waggle.messages.tool.authoring import ToolScope

_REQUESTER = CapabilitySet.parse("net:example.org", "fs:read:/work/**", "tool:request")


def _reasons(spec: ToolSpec) -> tuple[str, ...]:
    return validate_spec(spec, _REQUESTER).reasons


def test_a_spec_within_the_requesters_set_is_ok() -> None:
    result = validate_spec(make_tool_spec(), _REQUESTER)

    assert result.ok
    assert result.reasons == ()


def test_a_capability_outside_the_requesters_set_is_refused() -> None:
    spec = make_tool_spec(required_capabilities=("net:evil.example",))

    reasons = _reasons(spec)

    assert len(reasons) == 1
    assert "net:evil.example" in reasons[0]
    assert "outside the requester" in reasons[0]


def test_a_capability_covered_by_a_wider_held_grant_is_ok() -> None:
    requester = CapabilitySet.parse("net:*.example.org")
    spec = make_tool_spec(required_capabilities=("net:api.example.org",))

    assert validate_spec(spec, requester).ok


def test_an_unparseable_capability_is_a_reason_not_a_crash() -> None:
    spec = make_tool_spec(required_capabilities=("nonsense:thing",))

    reasons = _reasons(spec)

    assert len(reasons) == 1
    assert "does not parse" in reasons[0]


@pytest.mark.parametrize("capability", ["tool:other_tool", "tool:request", "tool:scope:cell"])
def test_a_tool_capability_is_refused_even_when_the_requester_holds_it(capability: str) -> None:
    requester = CapabilitySet.parse("tool:other_tool", "tool:request", "tool:scope:cell")
    spec = make_tool_spec(required_capabilities=(capability,))

    reasons = validate_spec(spec, requester).reasons

    assert any("never grants itself other tools" in reason for reason in reasons)


def test_every_reason_is_reported_not_only_the_first() -> None:
    spec = make_tool_spec(
        required_capabilities=("net:evil.example", "nonsense:x"),
        input_schema_json=json.dumps({"type": "object"}),
    )

    assert len(_reasons(spec)) == 3


@pytest.mark.parametrize(
    ("schema", "fragment"),
    [
        ({"type": "object"}, "additionalProperties"),
        ({"type": "object", "additionalProperties": True}, "additionalProperties"),
        ({"type": "object", "additionalProperties": {"type": "string"}}, "additionalProperties"),
        ({"additionalProperties": False}, '"type": "object"'),
        ({"type": "array", "additionalProperties": False}, '"type": "object"'),
    ],
)
def test_a_schema_that_is_not_a_closed_object_is_refused(
    schema: dict[str, object], fragment: str
) -> None:
    spec = make_tool_spec(input_schema_json=json.dumps(schema))

    reasons = _reasons(spec)

    assert len(reasons) == 1
    assert fragment in reasons[0]


def test_a_closed_object_schema_with_properties_is_ok() -> None:
    schema = {**DEFAULT_SCHEMA, "required": ["text"]}

    assert validate_spec(make_tool_spec(input_schema_json=json.dumps(schema)), _REQUESTER).ok


@pytest.mark.parametrize("text", ["not json", "[1, 2]", "42"])
def test_an_unvalidated_spec_with_a_non_object_schema_is_refused(text: str) -> None:
    # model_copy skips validation: the pure function must not trust that the model ran.
    spec = make_tool_spec().model_copy(update={"input_schema_json": text})

    reasons = _reasons(spec)

    assert len(reasons) == 1
    assert "JSON" in reasons[0]


@pytest.mark.parametrize("entry_point", ["run", "Tool:run", "other:run", "tool:run:x"])
def test_a_bad_entry_point_is_refused(entry_point: str) -> None:
    spec = make_tool_spec().model_copy(update={"entry_point": entry_point})

    assert len(_reasons(spec)) == 1


def test_a_cell_scope_spec_without_a_cell_is_refused() -> None:
    spec = make_tool_spec().model_copy(update={"scope": ToolScope.CELL})

    assert any("must name its Cell" in reason for reason in _reasons(spec))


def test_a_hive_scope_spec_naming_a_cell_is_refused() -> None:
    cell_spec = make_tool_spec(scope=ToolScope.CELL)
    spec = cell_spec.model_copy(update={"scope": ToolScope.HIVE})

    assert any("must not name a Cell" in reason for reason in _reasons(spec))


def test_a_spec_with_no_platform_is_refused() -> None:
    spec = make_tool_spec().model_copy(update={"supported_os": ()})

    assert any("operating system" in reason for reason in _reasons(spec))


def test_validate_spec_is_deterministic() -> None:
    spec = make_tool_spec(required_capabilities=("net:evil.example",))

    assert _reasons(spec) == _reasons(spec)
