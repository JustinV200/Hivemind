"""Tests for hivemind.royal_jelly.scaffold.models: the brief, the model's reply and the result.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/scaffold/models.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.scaffold.models for the module under test.
"""

from __future__ import annotations

import json

import pytest
from builders.royal_jelly import (
    make_brief,
    make_failing_report,
    make_platform,
    make_tool_request,
    make_tool_spec,
)
from pydantic import ValidationError

from hivemind.royal_jelly.scaffold import ScaffoldReply, ToolRequestBrief
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.messages.tool.authoring import ToolScope

_SCHEMA = json.dumps({"type": "object", "additionalProperties": False})


def _reply(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "description": "Echo.",
        "input_schema_json": _SCHEMA,
        "test_source": "import unittest\n",
        "tool_source": "async def run(session, arguments):\n    return {}\n",
    }
    fields.update(overrides)
    return fields


def test_brief_round_trips_through_json() -> None:
    brief = make_brief()

    assert ToolRequestBrief.model_validate_json(brief.model_dump_json()) == brief


def test_brief_refuses_a_cell_scope_without_a_cell() -> None:
    fields = {**make_brief().model_dump(), "scope": ToolScope.CELL, "cell_id": None}

    with pytest.raises(ValidationError, match="cell_id"):
        ToolRequestBrief.model_validate(fields)


def test_brief_refuses_a_hive_scope_naming_a_cell() -> None:
    fields = {**make_brief().model_dump(), "cell_id": new_cell_id(FakeClock())}

    with pytest.raises(ValidationError, match="cell_id"):
        ToolRequestBrief.model_validate(fields)


def test_brief_refuses_an_unparseable_capability() -> None:
    with pytest.raises(ValidationError, match="unparseable"):
        make_brief(required_capabilities=("not a capability",))


def test_with_retry_carries_the_report_and_bounds_the_source() -> None:
    brief = make_brief()
    spec = make_tool_spec()
    report = make_failing_report(spec, "a" * 64)

    retry = brief.with_retry(report, "x" * 100_000)

    assert retry.previous_report == report
    assert retry.previous_tool_source is not None
    assert len(retry.previous_tool_source) < 100_000
    assert brief.previous_report is None  # the original is untouched (frozen values)


def test_from_request_copies_the_hive_facts_and_takes_held_capabilities() -> None:
    request = make_tool_request()

    brief = ToolRequestBrief.from_request(request, held=("fs:read:**",))

    assert (brief.tool_id, brief.requested_by, brief.name) == (
        request.tool_id,
        request.warden_id,
        request.name,
    )
    assert brief.required_capabilities == ("fs:read:**",)
    assert brief.platform == request.platform


def test_reply_accepts_a_well_formed_object_and_defaults_the_rest() -> None:
    reply = ScaffoldReply.model_validate(_reply())

    assert reply.version == "1.0.0"
    assert reply.required_capabilities == ()
    assert reply.notes == ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"input_schema_json": "[1, 2]"},
        {"input_schema_json": "not json"},
        {"version": "v1"},
        {"tool_source": ""},
        {"name": "sneaky"},
        {"scope": "HIVE"},
        {"cell_id": "cell_01"},
    ],
)
def test_reply_refuses_malformed_or_identity_claiming_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ScaffoldReply.model_validate(_reply(**overrides))


def test_platform_builder_matches_the_sandbox_image() -> None:
    assert make_platform().distribution == "ubuntu"
