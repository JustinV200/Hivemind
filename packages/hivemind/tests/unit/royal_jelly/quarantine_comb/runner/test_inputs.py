"""Test input derivation: sample and fuzz inputs always validate against their own schema.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/runner/inputs.py
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/runner/inputs.py for the module under test.
"""

from __future__ import annotations

import json
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from hivemind.llm.ladders.extraction import validate_arguments
from hivemind.royal_jelly.quarantine_comb.runner.inputs import (
    LONG_STRING_CHARS,
    derive_fuzz_inputs,
    sample_input,
)

_NAMES = st.text(alphabet="abcdefgh", min_size=1, max_size=4)
_BOUND = st.integers(min_value=-1_000, max_value=1_000)


@st.composite
def _property(draw: st.DrawFn) -> dict[str, Any]:
    """Draw one property subschema: a type, sometimes bounds, sometimes an enum or a default."""
    kind = draw(
        st.sampled_from(["string", "integer", "number", "boolean", "array", "object", "null"])
    )
    sub: dict[str, Any] = {"type": kind}
    if kind == "string" and draw(st.booleans()):
        low = draw(st.integers(min_value=0, max_value=6))
        sub.update(minLength=low, maxLength=low + draw(st.integers(min_value=0, max_value=6)))
    if kind in ("integer", "number") and draw(st.booleans()):
        low = draw(_BOUND)
        sub.update(minimum=low, maximum=low + draw(st.integers(min_value=0, max_value=500)))
    if kind == "string" and draw(st.booleans()):
        sub["enum"] = draw(st.lists(st.text(max_size=5), min_size=1, max_size=4, unique=True))
    if kind == "array" and draw(st.booleans()):
        sub["items"] = {"type": draw(st.sampled_from(["string", "integer"]))}
    return sub


@st.composite
def _schemas(draw: st.DrawFn) -> dict[str, Any]:
    """Draw a small closed object schema with some required properties."""
    names = draw(st.lists(_NAMES, unique=True, max_size=5))
    properties = {name: draw(_property()) for name in names}
    required = draw(st.lists(st.sampled_from(names), unique=True)) if names else []
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


@settings(max_examples=60, deadline=None)
@given(schema=_schemas(), count=st.integers(min_value=1, max_value=12))
def test_every_generated_input_validates_against_its_schema(
    schema: dict[str, Any], count: int
) -> None:
    inputs = derive_fuzz_inputs(schema, count)

    assert len(inputs) == count
    for case in inputs:
        assert validate_arguments(schema, case) == ()
        assert set(schema["required"]) <= set(case)
        assert set(case) <= set(schema["properties"])
        json.dumps(case, allow_nan=False)


@settings(max_examples=60, deadline=None)
@given(schema=_schemas())
def test_the_sample_input_validates_and_names_every_property(schema: dict[str, Any]) -> None:
    sample = sample_input(schema)

    assert validate_arguments(schema, sample) == ()
    assert set(sample) == set(schema["properties"])


@settings(max_examples=30, deadline=None)
@given(schema=_schemas(), seed=st.integers())
def test_the_same_schema_and_seed_give_the_same_inputs(schema: dict[str, Any], seed: int) -> None:
    assert derive_fuzz_inputs(schema, 6, seed) == derive_fuzz_inputs(schema, 6, seed)


def test_the_first_case_is_the_required_properties_only_and_the_second_has_all() -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        "required": ["a"],
        "additionalProperties": False,
    }

    first, second, *_ = derive_fuzz_inputs(schema, 4)

    assert set(first) == {"a"}
    assert set(second) == {"a", "b"}


def test_edge_values_cover_the_empty_the_long_and_the_huge() -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"s": {"type": "string"}, "n": {"type": "integer"}},
        "required": ["s", "n"],
        "additionalProperties": False,
    }

    cases = derive_fuzz_inputs(schema, 200)

    strings = {case["s"] for case in cases}
    numbers = {case["n"] for case in cases}
    assert "" in strings
    assert "a" * LONG_STRING_CHARS in strings
    assert {0, -1} <= numbers
    assert any(isinstance(n, int) and abs(n) >= 2**31 for n in numbers)


def test_defaults_and_enums_are_preferred_for_the_typical_value() -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["fast", "slow"]},
            "n": {"type": "integer", "default": 7},
        },
        "additionalProperties": False,
    }

    assert sample_input(schema) == {"mode": "fast", "n": 7}


def test_a_schema_without_properties_gives_empty_inputs() -> None:
    assert derive_fuzz_inputs({"type": "object"}, 3) == [{}, {}, {}]
    assert sample_input({}) == {}
