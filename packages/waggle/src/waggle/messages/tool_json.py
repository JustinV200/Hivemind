"""Validate the JSON text the tool family carries, without ever handling it as a mapping.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Tool
schemas, arguments and outputs are inherently open JSON, so the tool family is the one sanctioned
use of JSON text on the wire (spec section 8): each such field is a bounded ``str`` that must
parse as a JSON object (a schema, an argument document) or as any JSON value (an output), and is
validated against the tool's schema at the edge (the Warden that runs the call, one of the
always-on supervisors of a Cell, a unit of compute), never handled as a mapping in transit. The
two checks here prove the text parses and return it unchanged, so the parsed value is dropped
on the spot: a ``dict[str, Any]`` never leaves the codec that produced it (codingrules section
9), and a message field is text end to end. They are split out of ``waggle.messages.tool`` by
responsibility so each file stays under the codingrules 5.1 size limit.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages.tool (ToolPromoted's
    schema) and waggle.messages.tool_call (ToolInvoke's arguments, ToolResult's output) as
    AfterValidators on their text fields; calls into the standard library's json only.

Key invariants:
    - Both checks return exactly the string they were given; the parsed value never escapes.
    - Every decoder failure, a RecursionError from pathological nesting included, surfaces as a
      ValueError so pydantic reports a validation error rather than letting a crash escape.

See Also:
    - docs/waggle/spec.md section 8.8 for the fields these checks guard and their bounds.
    - waggle.messages.tool and waggle.messages.tool_call for the fields that carry them.
"""

from __future__ import annotations

import json

__all__ = ["check_json_object_text", "check_json_value_text"]


def check_json_object_text(value: str) -> str:
    """Return ``value`` when it parses as a JSON object; raise ValueError otherwise.

    The check for a tool's input schema and a call's argument document: both are objects by
    contract, and a bare array or scalar in their place is a sender bug the edge should never
    have to reason about.

    Args:
        value: The JSON text a field validator received.

    Returns:
        ``value`` unchanged; the parsed object is discarded so the field stays text.

    Raises:
        ValueError: ``value`` is not JSON, or is JSON but not an object; pydantic turns this into
            a validation error on the field.
    """
    parsed = _parse_json_text(value)
    # A schema or an argument document is always an object; anything else parsed but is the
    # wrong shape, which the edge's schema validation would report far less clearly.
    if not isinstance(parsed, dict):
        raise ValueError(
            f"The text must be a JSON object, got a JSON {type(parsed).__name__} instead."
        )
    return value


def check_json_value_text(value: str) -> str:
    """Return ``value`` when it is empty or parses as any JSON value; raise ValueError otherwise.

    The check for a tool's output: a tool may legitimately return a string, a number or a list,
    and an empty string means there was no output at all.

    Args:
        value: The JSON text a field validator received, or the empty string.

    Returns:
        ``value`` unchanged; the parsed value is discarded so the field stays text.

    Raises:
        ValueError: ``value`` is non-empty and not JSON; pydantic turns this into a validation
            error on the field.
    """
    # Empty means "no output" (spec 8.8, ToolResult.output_json); it is not JSON and must not be
    # parsed as such.
    if value == "":
        return value
    _parse_json_text(value)
    return value


def _parse_json_text(value: str) -> object:
    """Parse ``value`` as JSON, turning every decoder failure into a ValueError for pydantic."""
    # RecursionError is caught alongside JSONDecodeError because a peer can nest brackets deeper
    # than the decoder tolerates well inside the field's size cap, and a RecursionError would
    # escape pydantic as a crash instead of a validation error on the field.
    try:
        parsed: object = json.loads(value)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"The text is not valid JSON: {exc}") from exc
    return parsed
