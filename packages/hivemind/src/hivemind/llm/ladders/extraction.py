"""Extract fenced JSON/tool blocks from model text, render prompted preambles, validate arguments.

The PROMPTED rung of `hivemind.llm.ladders.structured` and the prompted tool protocol of
`hivemind.llm.ladders.tools` both exist for a model with no native schema output, JSON mode or
tool-call protocol: everything has to travel as plain text, in and out. This module is the plain
text half of that: `render_json_preamble`/`render_tool_preamble` build the instructions a prompted
request appends (what shape to reply in), `extract_json_block`/`extract_tool_blocks` pull the
model's fenced replies back out, and `validate_arguments` checks a tool call's `arguments` against
its `ToolDefinition.parameters` JSON schema using a small, documented subset -- not a `jsonschema`
dependency (see `docs/adr/0009-structured-output-and-tool-call-degradation-ladders.md` for why).

The supported schema subset: `type` (`object`, `array`, `string`, `integer`, `number`, `boolean`,
`null`) checked at the top level and, one level deep, on every entry of `properties`; `required`
(top level); `additionalProperties: false` (top level); `enum` (top level and one level deep, on a
`properties` entry). Every other JSON-schema keyword (`minimum`, `pattern`, nested `properties`
inside a property's own subschema, `oneOf`, and so on) is read by nothing here and is silently
ignored, never rejected -- a schema written for a stricter validator still round-trips through this
one without becoming invalid.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.ladders`. Called by
    `hivemind.llm.ladders.structured` (the JSON preamble and extraction) and
    `hivemind.llm.ladders.tools` (the tool preamble, extraction and argument validation). Calls
    into `hivemind.llm.models` only.

Key invariants:
    - `extract_json_block` returns the first ```json fenced block's content, stripped of leading
      and trailing whitespace, or `None` when no such block is present; it never raises on
      malformed input, since a model's reply is untrusted text (codingrules section 15).
    - `extract_tool_blocks` returns every ```tool fenced block's content, in the order they
      appear, and an empty tuple when none are found -- never `None`, since "no tool calls this
      round" is itself a meaningful, valid result.
    - `validate_arguments` never raises: an unparseable or partially-unsupported schema simply
      produces fewer findings, never an exception, since a malformed schema is the tool author's
      problem to fix, not a reason to crash a running tool loop.

See Also:
    - docs/adr/0009-structured-output-and-tool-call-degradation-ladders.md for why this module
      implements a schema subset instead of depending on `jsonschema`.
    - .claude/codingrules.md section 15 for "LLM output is untrusted input".
    - hivemind.llm.models for JsonObject and ToolDefinition, the types this module reads.
    - hivemind.llm.ladders.structured and hivemind.llm.ladders.tools for this module's two callers.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from hivemind.llm.models import JsonObject, ToolDefinition

# Non-greedy so a reply with more than one fenced block never lets the first one swallow the rest;
# DOTALL so a block's JSON body (which usually spans several lines) matches across newlines.
_JSON_FENCE = re.compile(r"```json[ \t]*\r?\n(.*?)```", re.DOTALL)
_TOOL_FENCE = re.compile(r"```tool[ \t]*\r?\n(.*?)```", re.DOTALL)

# The documented schema subset's type keyword, mapped to a runtime type check. bool is checked
# before int/float explicitly: Python's bool is a subclass of int, so `isinstance(True, int)` is
# True and would otherwise let a boolean silently pass an "integer" or "number" check.
_TYPE_CHECKS: dict[str, Callable[[object], bool]] = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}

__all__ = [
    "extract_json_block",
    "extract_tool_blocks",
    "render_json_preamble",
    "render_tool_preamble",
    "validate_arguments",
]


def extract_json_block(text: str) -> str | None:
    """Return the first ```json fenced block's content in `text`, or None if there is none.

    Args:
        text: A model's reply, expected to contain exactly one fenced ```json block (the PROMPTED
            rung's own instruction), but read defensively: only the first is used.

    Returns:
        The block's content, stripped of leading/trailing whitespace, or None.
    """
    match = _JSON_FENCE.search(text)
    return match.group(1).strip() if match else None


def extract_tool_blocks(text: str) -> tuple[str, ...]:
    """Return every ```tool fenced block's content in `text`, in the order they appear.

    Args:
        text: A model's reply under the prompted tool protocol; zero, one, or many fenced ```tool
            blocks are all valid (zero means the model made no tool call this round).

    Returns:
        Each block's content, stripped of leading/trailing whitespace; an empty tuple if none.
    """
    return tuple(match.strip() for match in _TOOL_FENCE.findall(text))


def render_json_preamble(schema: JsonObject) -> str:
    """Build the PROMPTED rung's instruction: reply with one fenced ```json block matching `schema`.

    Args:
        schema: The JSON schema the reply must satisfy (`type[ModelT].model_json_schema()`).

    Returns:
        Plain text, no vendor-specific tags, appended as a user turn by
        `hivemind.llm.ladders.structured`.
    """
    schema_text = json.dumps(schema, indent=2, sort_keys=True)
    return (
        "Reply with exactly one fenced ```json code block, and nothing else outside it, "
        "containing valid JSON that matches this schema:\n\n"
        f"```json\n{schema_text}\n```"
    )


def render_tool_preamble(tools: tuple[ToolDefinition, ...]) -> str:
    """Build the prompted tool protocol's instruction: list every tool and the fenced call format.

    Args:
        tools: The tools this call may use.

    Returns:
        Plain text, no vendor-specific tags, appended as a user turn by
        `hivemind.llm.ladders.tools`.
    """
    sections = "\n\n".join(_render_one_tool(tool) for tool in tools)
    return (
        "You may call any of the following tools. To call one, reply with a fenced ```tool code "
        'block containing exactly {"name": "<tool name>", "arguments": {<arguments object>}}. '
        "You may include more than one such block in one reply to call several tools at once. "
        "Once you have your final answer, reply with plain text and no ```tool block.\n\n"
        f"{sections}"
    )


def validate_arguments(schema: JsonObject, arguments: JsonObject) -> tuple[str, ...]:
    """Check `arguments` against `schema`'s documented subset, returning every violation found.

    See the module docstring for exactly which JSON-schema keywords this checks; every other
    keyword is ignored, never rejected.

    Args:
        schema: A tool's `ToolDefinition.parameters`, or any JSON schema shaped the same way.
        arguments: The candidate arguments to check.

    Returns:
        One message per violation found, in a fixed check order (type, enum, required,
        additionalProperties, then each declared property); an empty tuple means `arguments` is
        valid under the supported subset.
    """
    return (
        *_check_top_level_type(schema, arguments),
        *_check_top_level_enum(schema, arguments),
        *_check_required(schema, arguments),
        *_check_additional_properties(schema, arguments),
        *_check_properties(schema, arguments),
    )


def _render_one_tool(tool: ToolDefinition) -> str:
    """Render one tool's name, description and arguments schema for `render_tool_preamble`."""
    schema_text = json.dumps(tool.parameters, indent=2, sort_keys=True)
    return f"### {tool.name}\n{tool.description}\n\nArguments schema:\n```json\n{schema_text}\n```"


def _check_top_level_type(schema: JsonObject, arguments: JsonObject) -> list[str]:
    """Check `arguments` itself against `schema`'s top-level `type`, if it declares one."""
    expected = schema.get("type")
    if not isinstance(expected, str):
        return []  # No 'type' keyword (or not a recognised string): nothing to check.
    check = _TYPE_CHECKS.get(expected)
    if check is None or check(arguments):
        return []
    return [f"arguments must be of type {expected!r}."]


def _check_top_level_enum(schema: JsonObject, arguments: JsonObject) -> list[str]:
    """Check `arguments` itself against `schema`'s top-level `enum`, if it declares one."""
    return _check_enum("arguments", schema, arguments)


def _check_required(schema: JsonObject, arguments: JsonObject) -> list[str]:
    """Flag every name in `schema`'s top-level `required` that is missing from `arguments`."""
    required = schema.get("required")
    if not isinstance(required, list):
        return []
    return [
        f"missing required property {name!r}."
        for name in required
        if isinstance(name, str) and name not in arguments
    ]


def _check_additional_properties(schema: JsonObject, arguments: JsonObject) -> list[str]:
    """Flag every key in `arguments` not in `properties`, when `additionalProperties` is false."""
    if schema.get("additionalProperties") is not False:
        return []  # Keyword absent, or not literally False: additional properties are allowed.
    allowed = _property_names(schema)
    return [
        f"unexpected property {key!r}; additionalProperties is false."
        for key in arguments
        if key not in allowed
    ]


def _check_properties(schema: JsonObject, arguments: JsonObject) -> list[str]:
    """Check every declared property present in `arguments` against its own type and enum.

    One level deep only (the documented subset): a property's own `type`/`enum` are checked, but
    a nested object's `properties` are not recursed into.
    """
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    errors: list[str] = []
    for name, subschema in properties.items():
        # Absence is `required`'s job, not this check's; a non-dict subschema is malformed input
        # from the tool author, silently skipped rather than raised (module docstring's promise).
        if name not in arguments or not isinstance(subschema, dict):
            continue
        errors.extend(_check_property_type(name, subschema, arguments[name]))
        errors.extend(_check_enum(f"property {name!r}", subschema, arguments[name]))
    return errors


def _check_property_type(name: str, subschema: JsonObject, value: object) -> list[str]:
    """Check one property's value against its own subschema's `type`, if it declares one."""
    expected = subschema.get("type")
    if not isinstance(expected, str):
        return []
    check = _TYPE_CHECKS.get(expected)
    if check is None or check(value):
        return []
    return [f"property {name!r} must be of type {expected!r}."]


def _check_enum(label: str, schema: JsonObject, value: object) -> list[str]:
    """Check `value` against `schema`'s `enum`, if it declares one, labelling any error `label`."""
    enum_values = schema.get("enum")
    if not isinstance(enum_values, list):
        return []
    if value in enum_values:
        return []
    return [f"{label} must be one of {enum_values!r}."]


def _property_names(schema: JsonObject) -> frozenset[str]:
    """Return the names declared in `schema`'s `properties`, or an empty set if there are none."""
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return frozenset()
    return frozenset(properties)
