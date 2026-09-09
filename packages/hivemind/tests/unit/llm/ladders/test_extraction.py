"""Tests for hivemind.llm.ladders.extraction: fenced-block extraction, preambles, validation.

Fits into the Hive:
    Mirrors src/hivemind/llm/ladders/extraction.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.ladders.extraction for the module under test.
"""

from __future__ import annotations

from builders.llm import make_tool

from hivemind.llm.ladders.extraction import (
    extract_json_block,
    extract_tool_blocks,
    render_json_preamble,
    render_tool_preamble,
    validate_arguments,
)
from hivemind.llm.models import JsonObject

# ──────────────────────────────────────────────────────────────────────────────
# extract_json_block
# ──────────────────────────────────────────────────────────────────────────────


def test_extract_json_block_returns_the_fenced_content_stripped() -> None:
    text = 'Here you go:\n```json\n{"a": 1}\n```\nThanks.'

    assert extract_json_block(text) == '{"a": 1}'


def test_extract_json_block_returns_none_when_no_fenced_block_is_present() -> None:
    assert extract_json_block("just some plain text") is None


def test_extract_json_block_uses_the_first_block_when_more_than_one_is_present() -> None:
    text = '```json\n{"a": 1}\n```\n```json\n{"a": 2}\n```'

    assert extract_json_block(text) == '{"a": 1}'


# ──────────────────────────────────────────────────────────────────────────────
# extract_tool_blocks
# ──────────────────────────────────────────────────────────────────────────────


def test_extract_tool_blocks_returns_empty_tuple_when_none_are_present() -> None:
    assert extract_tool_blocks("no tool calls here") == ()


def test_extract_tool_blocks_returns_every_block_in_order() -> None:
    text = (
        '```tool\n{"name": "a", "arguments": {}}\n```\n```tool\n{"name": "b", "arguments": {}}\n```'
    )

    blocks = extract_tool_blocks(text)

    assert blocks == ('{"name": "a", "arguments": {}}', '{"name": "b", "arguments": {}}')


# ──────────────────────────────────────────────────────────────────────────────
# Preambles
# ──────────────────────────────────────────────────────────────────────────────


def test_render_json_preamble_embeds_the_schema_and_instructs_one_fenced_block() -> None:
    preamble = render_json_preamble({"type": "object", "properties": {"a": {"type": "string"}}})

    assert "```json" in preamble
    assert '"a"' in preamble
    assert "fenced" in preamble


def test_render_tool_preamble_lists_every_tools_name_and_description() -> None:
    tools = (make_tool(name="alpha", description="Does alpha things."), make_tool(name="beta"))

    preamble = render_tool_preamble(tools)

    assert "alpha" in preamble
    assert "Does alpha things." in preamble
    assert "beta" in preamble
    assert "```tool" in preamble


# ──────────────────────────────────────────────────────────────────────────────
# validate_arguments
# ──────────────────────────────────────────────────────────────────────────────

_SCHEMA: JsonObject = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "count": {"type": "integer", "minimum": 1000},  # 'minimum' is an unsupported keyword.
        "mode": {"type": "string", "enum": ["fast", "slow"]},
    },
    "required": ["name"],
    "additionalProperties": False,
}


def test_validate_arguments_accepts_a_fully_valid_object() -> None:
    errors = validate_arguments(_SCHEMA, {"name": "x", "count": 1, "mode": "fast"})

    assert errors == ()


def test_validate_arguments_flags_a_missing_required_property() -> None:
    errors = validate_arguments(_SCHEMA, {})

    assert any("name" in e for e in errors)


def test_validate_arguments_flags_a_wrong_property_type() -> None:
    errors = validate_arguments(_SCHEMA, {"name": 123})

    assert any("name" in e and "type" in e for e in errors)


def test_validate_arguments_flags_an_unexpected_property_when_additional_properties_is_false() -> (
    None
):
    errors = validate_arguments(_SCHEMA, {"name": "x", "extra": 1})

    assert any("extra" in e for e in errors)


def test_validate_arguments_flags_an_enum_violation() -> None:
    errors = validate_arguments(_SCHEMA, {"name": "x", "mode": "medium"})

    assert any("mode" in e for e in errors)


def test_validate_arguments_ignores_unsupported_keywords_like_minimum() -> None:
    # count=1, which violates the (unsupported) "minimum": 1000, but that keyword is ignored.
    errors = validate_arguments(_SCHEMA, {"name": "x", "count": 1})

    assert errors == ()


def test_validate_arguments_flags_a_top_level_type_mismatch() -> None:
    errors = validate_arguments({"type": "array"}, {"name": "x"})

    assert any("type" in e for e in errors)


def test_validate_arguments_checks_a_top_level_enum() -> None:
    errors = validate_arguments({"enum": [{"a": 1}]}, {"a": 2})

    assert any("arguments" in e for e in errors)


def test_validate_arguments_treats_a_bool_as_not_an_integer() -> None:
    # Python's bool is a subclass of int; the schema subset must not let True satisfy "integer".
    errors = validate_arguments({"properties": {"n": {"type": "integer"}}}, {"n": True})

    assert any("n" in e for e in errors)


def test_validate_arguments_ignores_a_schema_with_no_recognised_keywords() -> None:
    errors = validate_arguments({}, {"anything": "goes"})

    assert errors == ()


def test_validate_arguments_skips_the_type_check_for_a_property_with_no_type_keyword() -> None:
    schema: JsonObject = {"properties": {"mode": {"enum": ["fast", "slow"]}}}

    errors = validate_arguments(schema, {"mode": "fast"})

    assert errors == ()


def test_validate_arguments_allows_any_property_when_no_properties_are_declared_at_all() -> None:
    # additionalProperties: false with no "properties" key means "no properties allowed";
    # _property_names must still return an (empty) set rather than raising.
    errors = validate_arguments({"additionalProperties": False}, {"extra": 1})

    assert any("extra" in e for e in errors)
