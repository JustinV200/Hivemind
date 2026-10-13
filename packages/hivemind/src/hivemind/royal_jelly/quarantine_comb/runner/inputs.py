"""Derive inputs from a tool's JSON Schema: one sample input and a set of fuzz inputs.

The runner has no other source of truth about what a tool accepts than the schema in its spec, so
both checks are built from it. `sample_input` is the schema's own example (defaults and enums
first, then a typical value per type) and is what the schema check validates. `derive_fuzz_inputs`
makes many: each declared property filled with an edge value of its type (empty and very long
strings, zero, negative and huge integers, empty containers), required properties always present,
optional ones sometimes absent. Everything is generated from a seeded generator, so the same
schema always gives the same inputs and a failure reproduces. Every generated input satisfies the
subset of JSON Schema the Hive validates arguments against, so a rejection is the tool's choice,
not a malformed case.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Called by `runner.phases`; tested with hypothesis over small schemas. Calls into the standard
    library only (pydantic's JsonValue for the types).

Key invariants:
    - Pure and deterministic: schema and seed in, inputs out; no I/O, no clock, no global state.
    - Inputs only ever name properties the schema declares, and always include every required one.
    - Values respect `enum`, `minimum`/`maximum` and `minLength`/`maxLength` when present.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the schema and fuzz gates.
    - hivemind.llm.ladders.extraction.validate_arguments for the subset inputs must satisfy.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping

from pydantic import JsonValue

FUZZ_SEED = 20_261_012  # Fixed, so a failing fuzz case is reproducible from the report alone.
LONG_STRING_CHARS = 4_096  # Long enough to trip a naive fixed buffer, short enough to be cheap.
OPTIONAL_PRESENT_RATE = 0.6  # Share of optional properties a random case includes.
_BIG_INT = 2**63  # Past a 64-bit signed integer, where careless arithmetic overflows.
_STRINGS = ("", "a", " ", "0", "null", "Ünïcödé ☃", "line\nbreak", "a" * LONG_STRING_CHARS)
_INTEGERS = (0, 1, -1, 2**31, -(2**31), _BIG_INT)
_NUMBERS = (0.0, 1.5, -1.5, 1e9, -1e9, 1e-9)
_TYPICAL: Mapping[str, JsonValue] = {
    "string": "x",
    "integer": 1,
    "number": 1.5,
    "boolean": True,
    "array": [],
    "object": {},
    "null": None,
}

__all__ = ["FUZZ_SEED", "derive_fuzz_inputs", "sample_input"]


def sample_input(schema: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Build the schema's own example input: every property, each at its default or typical value.

    Args:
        schema: A tool's input schema (a JSON object schema).

    Returns:
        An input naming every declared property.
    """
    return {name: _typical(sub) for name, sub in _properties(schema).items()}


def derive_fuzz_inputs(
    schema: Mapping[str, JsonValue], count: int, seed: int = FUZZ_SEED
) -> list[dict[str, JsonValue]]:
    """Build `count` inputs that exercise edge values of every declared property.

    Args:
        schema: A tool's input schema.
        count: How many inputs to build (at least one is always returned when `count` is positive).
        seed: Seeds the generator; the same arguments always give the same inputs.

    Returns:
        The first input is required properties at typical values, the second every property at
        typical values, and the rest are random mixes of edge values.
    """
    rng = random.Random(seed)  # noqa: S311  # SAFETY: reproducible test data, not a secret.
    properties = _properties(schema)
    required = _required(schema)
    cases: list[dict[str, JsonValue]] = []
    for index in range(count):
        case: dict[str, JsonValue] = {}
        for name, sub in properties.items():
            if index == 0 and name not in required:
                continue
            if index > 1 and name not in required and rng.random() > OPTIONAL_PRESENT_RATE:
                continue
            case[name] = _typical(sub) if index < 2 else _edge(sub, rng)
        cases.append(case)
    return cases


def _properties(schema: Mapping[str, JsonValue]) -> dict[str, Mapping[str, JsonValue]]:
    """Return the schema's declared properties that are themselves objects."""
    declared = schema.get("properties")
    if not isinstance(declared, dict):
        return {}
    return {name: sub for name, sub in declared.items() if isinstance(sub, dict)}


def _required(schema: Mapping[str, JsonValue]) -> frozenset[str]:
    """Return the names the schema lists as required (those that are also declared or not)."""
    listed = schema.get("required")
    if not isinstance(listed, list):
        return frozenset()
    return frozenset(name for name in listed if isinstance(name, str))


def _kind(sub: Mapping[str, JsonValue]) -> str:
    """Return the schema's type name, taking the first non-null of a union, `string` if absent."""
    declared = sub.get("type")
    if isinstance(declared, str):
        return declared
    if isinstance(declared, list):
        names = [name for name in declared if isinstance(name, str) and name != "null"]
        return names[0] if names else "null"
    return "string"


def _typical(sub: Mapping[str, JsonValue]) -> JsonValue:
    """Return the property's default, else its first enum value, else a typical one."""
    if "default" in sub:
        return sub["default"]
    options = sub.get("enum")
    if isinstance(options, list) and options:
        return options[0]
    return _fit(_TYPICAL.get(_kind(sub), "x"), sub)


def _edge(sub: Mapping[str, JsonValue], rng: random.Random) -> JsonValue:
    """Return a random edge value for the property, within its enum and bounds."""
    options = sub.get("enum")
    if isinstance(options, list) and options:
        return rng.choice(options)
    kind = _kind(sub)
    pool: tuple[JsonValue, ...]
    if kind == "string":
        pool = _STRINGS
    elif kind == "integer":
        pool = _INTEGERS
    elif kind == "number":
        pool = _NUMBERS
    elif kind == "boolean":
        pool = (True, False)
    elif kind == "array":
        pool = ([], [_typical(item)] if isinstance(item := sub.get("items"), dict) else [])
    else:
        pool = (_TYPICAL.get(kind),)
    return _fit(rng.choice(pool), sub)


def _fit(value: JsonValue, sub: Mapping[str, JsonValue]) -> JsonValue:
    """Bring `value` inside the schema's length and numeric bounds, when it declares any."""
    if isinstance(value, str):
        return _fit_string(value, sub)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return value
    low, high = sub.get("minimum"), sub.get("maximum")
    if isinstance(low, int | float) and not isinstance(low, bool) and value < low:
        value = math.ceil(low) if isinstance(value, int) else float(low)
    if isinstance(high, int | float) and not isinstance(high, bool) and value > high:
        value = math.floor(high) if isinstance(value, int) else float(high)
    return value


def _fit_string(value: str, sub: Mapping[str, JsonValue]) -> str:
    """Pad or cut a string to the schema's `minLength` and `maxLength`."""
    longest, shortest = sub.get("maxLength"), sub.get("minLength")
    if isinstance(longest, int) and not isinstance(longest, bool):
        value = value[: max(0, longest)]
    if isinstance(shortest, int) and not isinstance(shortest, bool) and len(value) < shortest:
        value += "a" * (shortest - len(value))
    return value
