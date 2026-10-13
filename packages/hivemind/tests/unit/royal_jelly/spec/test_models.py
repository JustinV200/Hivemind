"""Tests for hivemind.royal_jelly.spec.models: ToolSpec's bounds, scope rule and canonical hash.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/spec/models.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.spec.models for the module under test.
"""

from __future__ import annotations

import json

import pytest
from builders.royal_jelly import make_tool_spec
from pydantic import ValidationError

from hivemind.royal_jelly.spec import ToolSpec, spec_sha256
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.messages.labels import OsFamily
from waggle.messages.tool.authoring import MAX_INPUT_SCHEMA_CHARS, ToolScope


def test_spec_round_trips_through_its_canonical_json() -> None:
    spec = make_tool_spec(dependencies=("requests",), supported_os=(OsFamily.LINUX, OsFamily.MACOS))

    assert ToolSpec.from_json(spec.to_json()) == spec


def test_to_json_is_compact_and_key_sorted() -> None:
    spec = make_tool_spec()

    text = spec.to_json()

    assert text == json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))


def test_spec_sha256_is_the_digest_of_the_canonical_text_and_stable() -> None:
    spec = make_tool_spec()

    assert spec_sha256(spec) == spec_sha256(ToolSpec.from_json(spec.to_json()))
    assert len(spec_sha256(spec)) == 64


def test_spec_sha256_changes_when_any_field_changes() -> None:
    spec = make_tool_spec()

    other = spec.model_copy(update={"description": "Something else."})

    assert spec_sha256(other) != spec_sha256(spec)


def test_entry_point_defaults_to_tool_run() -> None:
    assert make_tool_spec().entry_point == "tool:run"


@pytest.mark.parametrize(
    "entry_point", ["tool", "Tool:run", "tool:run:x", "tool.py:run", ":run", "tool:"]
)
def test_entry_point_pattern_rejects_malformed_text(entry_point: str) -> None:
    with pytest.raises(ValidationError):
        make_tool_spec(entry_point=entry_point)


def test_cell_scope_requires_a_cell() -> None:
    with pytest.raises(ValidationError, match="cell_id"):
        ToolSpec.model_validate(
            {**make_tool_spec().model_dump(), "scope": ToolScope.CELL, "cell_id": None}
        )


def test_hive_scope_rejects_a_cell() -> None:
    with pytest.raises(ValidationError, match="cell_id"):
        make_tool_spec(cell_id=new_cell_id(FakeClock()))


def test_cell_scope_builder_mints_a_cell() -> None:
    spec = make_tool_spec(scope=ToolScope.CELL)

    assert spec.cell_id is not None


def test_supported_os_must_be_non_empty_and_unique() -> None:
    with pytest.raises(ValidationError):
        make_tool_spec(supported_os=())
    with pytest.raises(ValidationError, match="unique"):
        make_tool_spec(supported_os=(OsFamily.LINUX, OsFamily.LINUX))


def test_dependencies_are_capped_at_sixteen() -> None:
    assert make_tool_spec(dependencies=tuple(f"pkg{i}" for i in range(16)))
    with pytest.raises(ValidationError):
        make_tool_spec(dependencies=tuple(f"pkg{i}" for i in range(17)))


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "Bad-Name"},
        {"version": "1.0"},
        {"description": ""},
        {"input_schema_json": "[]"},
        {"input_schema_json": "{" + " " * MAX_INPUT_SCHEMA_CHARS + "}"},
        {"unknown_field": 1},
    ],
)
def test_spec_rejects_a_malformed_field(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_tool_spec(**overrides)


def test_spec_is_frozen() -> None:
    spec = make_tool_spec()

    with pytest.raises(ValidationError):
        spec.name = "other"  # type: ignore[misc]  # frozen on purpose; the test proves it
