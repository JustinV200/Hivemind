"""Validate a JSON value against the JSON Schema subset the Landing Board's document uses.

The conformance client (a program written from ``docs/entrance/openapi.json`` alone, roadmap step
10.5c) checks every body it sends and every body it receives against the schema the committed
document declares for it. ``jsonschema`` is not in the lockfile and nothing may be added, so this
module implements exactly the keywords the document uses (OpenAPI 3.1 schemas are JSON Schema
2020-12): ``$ref`` into the document, ``anyOf``, ``type``, ``enum``, ``const``, ``properties``,
``required``, ``additionalProperties``, ``items``, ``maxItems``, the string and number bounds,
``pattern`` and the ``date-time`` format. A keyword it does not know fails the check rather than
being skipped, so the document can never quietly outgrow the validator; an OpenAPI extension
(``x-``) describes and never constrains, and an enum marked ``x-hive-open`` accepts a member of its
own type that was added after the client was written (the document's ``x-hive-versioning``).

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Called by
    ``e2e.landing_client.document``; imports the standard library only.

Key invariants:
    - A schema keyword outside the known set raises ``SchemaError``; nothing is silently ignored.
    - Validation never mutates the value or the schema.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

# Keywords that describe a value and never constrain it (JSON Schema's annotations).
_ANNOTATIONS = frozenset({"title", "description", "default", "writeOnly", "readOnly", "examples"})
# JSON's types, as Python's json module decodes them; a bool is never a number here.
_TYPES: Mapping[str, Callable[[object], bool]] = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, int | float) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
}

__all__ = ["SchemaError", "validate"]


class SchemaError(AssertionError):
    """A value the document's schema does not admit, naming where in the value and why."""


def validate(
    value: object, schema: Mapping[str, object], document: Mapping[str, object], where: str = "$"
) -> None:
    """Check ``value`` against ``schema``, resolving every ``$ref`` inside ``document``.

    Args:
        value: A decoded JSON value (a response body, a request body, a stream frame).
        schema: The schema the document declares for it.
        document: The whole OpenAPI document, where ``#/components/...`` references point.
        where: The value's path, for the error message; ``$`` for the whole value.

    Raises:
        SchemaError: The value breaks the schema, or the schema uses a keyword this validator
            does not implement.
    """
    _check(value, schema, _At(document, where))


@dataclass(frozen=True, slots=True)
class _At:
    """Where a check stands: the document references resolve in, and the path into the value."""

    document: Mapping[str, object]
    where: str

    def into(self, step: str) -> _At:
        """Return the position one step deeper into the value."""
        return _At(self.document, f"{self.where}{step}")


def _check(value: object, schema: Mapping[str, object], at: _At) -> None:
    """Apply every keyword of ``schema`` to ``value``."""
    # A $ref may carry annotations beside it (the document does): the target decides the rest.
    reference = schema.get("$ref")
    if isinstance(reference, str):
        _check(value, _resolve(reference, at.document), at)
    for keyword, argument in schema.items():
        # An x- keyword is an OpenAPI extension: it describes, it never constrains.
        if keyword == "$ref" or keyword in _ANNOTATIONS or keyword.startswith("x-"):
            continue
        checker = _KEYWORDS.get(keyword)
        if checker is None:
            raise SchemaError(f"{at.where}: this validator does not implement {keyword!r}")
        checker(value, argument, schema, at)


def _resolve(reference: str, document: Mapping[str, object]) -> Mapping[str, object]:
    """Follow a local JSON pointer (``#/components/schemas/Name``) to the schema it names."""
    if not reference.startswith("#/"):
        raise SchemaError(f"only local references are expected, not {reference!r}")
    node: object = document
    # JSON pointer segments escape "/" as "~1" and "~" as "~0" (RFC 6901), in that order.
    for segment in reference[2:].split("/"):
        key = segment.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, Mapping) or key not in node:
            raise SchemaError(f"the reference {reference!r} points at nothing")
        node = node[key]
    if not isinstance(node, Mapping):
        raise SchemaError(f"the reference {reference!r} is not a schema")
    return node


def _schema(argument: object) -> Mapping[str, object]:
    """Narrow a keyword argument that must itself be a schema."""
    if not isinstance(argument, Mapping):
        raise SchemaError(f"expected a schema, found {argument!r}")
    return argument


def _fail(at: _At, message: str) -> None:
    """Raise the error for one broken keyword, at its position."""
    raise SchemaError(f"{at.where}: {message}")


# ──────────────────────────────────────────────────────────────────────────────
# Keywords: each checks one constraint, and only on the JSON type it applies to
# ──────────────────────────────────────────────────────────────────────────────


def _type(value: object, argument: object, _schema_: Mapping[str, object], at: _At) -> None:
    """``type``: one type name, or a list of them."""
    names = argument if isinstance(argument, list) else [argument]
    unknown = [name for name in names if str(name) not in _TYPES]
    if unknown:
        raise SchemaError(f"{at.where}: unknown JSON type {unknown!r}")
    if not any(_TYPES[str(name)](value) for name in names):
        _fail(at, f"{value!r} is not of type {argument!r}")


def _enum(value: object, argument: object, schema: Mapping[str, object], at: _At) -> None:
    """``enum``: one of the listed values (a bool never equals 0 or 1 here).

    An enum the document marks ``x-hive-open`` may gain members within ``/v1/`` (its
    ``x-hive-versioning``), so a value of the members' own type that the list does not name is a
    member added after this client was written, not an error.
    """
    members = argument if isinstance(argument, list) else []
    if any(type(value) is type(member) and value == member for member in members):
        return
    if schema.get("x-hive-open") is True and any(type(value) is type(m) for m in members):
        return
    _fail(at, f"{value!r} is not one of {members!r}")


def _const(value: object, argument: object, _schema_: Mapping[str, object], at: _At) -> None:
    """``const``: exactly this value."""
    if type(value) is not type(argument) or value != argument:
        _fail(at, f"{value!r} is not {argument!r}")


def _any_of(value: object, argument: object, _schema_: Mapping[str, object], at: _At) -> None:
    """``anyOf``: at least one branch admits the value."""
    branches = argument if isinstance(argument, list) else []
    for branch in branches:
        try:
            _check(value, _schema(branch), at)
        except SchemaError:
            continue
        return
    _fail(at, f"{value!r} matches none of its {len(branches)} alternatives")


def _properties(value: object, argument: object, _schema_: Mapping[str, object], at: _At) -> None:
    """``properties``: each named member that is present matches its own schema."""
    if not isinstance(value, dict):
        return
    for name, member in _schema(argument).items():
        if name in value:
            _check(value[name], _schema(member), at.into(f".{name}"))


def _required(value: object, argument: object, _schema_: Mapping[str, object], at: _At) -> None:
    """``required``: every listed member is present."""
    if isinstance(value, dict) and isinstance(argument, list):
        missing = [name for name in argument if name not in value]
        if missing:
            _fail(at, f"missing required members {missing!r}")


def _additional(value: object, argument: object, schema: Mapping[str, object], at: _At) -> None:
    """``additionalProperties``: members ``properties`` does not name are refused or checked."""
    if not isinstance(value, dict):
        return
    named = schema.get("properties")
    extra = [name for name in value if not isinstance(named, Mapping) or name not in named]
    # False refuses every stray member; a schema checks each one.
    if argument is False and extra:
        _fail(at, f"members {extra!r} are not declared")
    if isinstance(argument, Mapping):
        for name in extra:
            _check(value[name], argument, at.into(f".{name}"))


def _items(value: object, argument: object, _schema_: Mapping[str, object], at: _At) -> None:
    """``items``: every element matches the one item schema."""
    if isinstance(value, list):
        for index, element in enumerate(value):
            _check(element, _schema(argument), at.into(f"[{index}]"))


def _bound(
    measure: Callable[[object], float | None], holds: Callable[[float, float], bool], label: str
) -> _Checker:
    """Build a bound keyword from what it measures (a length, a count, a value) and its test."""

    def checker(value: object, argument: object, _schema_: Mapping[str, object], at: _At) -> None:
        """Apply the bound when it applies to this value's type."""
        measured = measure(value)
        if measured is None or not isinstance(argument, int | float):
            return
        if not holds(measured, float(argument)):
            _fail(at, f"{value!r} breaks {label} {argument!r}")

    return checker


def _pattern(value: object, argument: object, _schema_: Mapping[str, object], at: _At) -> None:
    """``pattern``: an ECMA-262 regular expression, unanchored; the document's are ^...$."""
    if isinstance(value, str) and re.search(str(argument), value) is None:
        _fail(at, f"{value!r} does not match {argument!r}")


def _format(value: object, argument: object, _schema_: Mapping[str, object], at: _At) -> None:
    """``format``: ``date-time`` (RFC 3339, with an offset) is checked; others annotate."""
    if argument != "date-time" or not isinstance(value, str):
        return
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        _fail(at, f"{value!r} is not an RFC 3339 date-time with an offset")


def _length(value: object) -> float | None:
    """Measure a string's length in code points (JSON Schema's unit); None for anything else."""
    return float(len(value)) if isinstance(value, str) else None


def _count(value: object) -> float | None:
    """Measure an array's length; None for anything else."""
    return float(len(value)) if isinstance(value, list) else None


def _number(value: object) -> float | None:
    """Read a JSON number; None for anything else (a bool included)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


# One keyword's check: the value, the keyword's argument, the whole schema, the position.
type _Checker = Callable[[object, object, Mapping[str, object], _At], None]

_KEYWORDS: Mapping[str, _Checker] = {
    "type": _type,
    "enum": _enum,
    "const": _const,
    "anyOf": _any_of,
    "properties": _properties,
    "required": _required,
    "additionalProperties": _additional,
    "items": _items,
    "pattern": _pattern,
    "format": _format,
    # Bounds apply only to their own type: a length to a string, a count to an array.
    "minLength": _bound(_length, operator.ge, "minLength"),
    "maxLength": _bound(_length, operator.le, "maxLength"),
    "maxItems": _bound(_count, operator.le, "maxItems"),
    "minimum": _bound(_number, operator.ge, "minimum"),
    "maximum": _bound(_number, operator.le, "maximum"),
    "exclusiveMinimum": _bound(_number, operator.gt, "exclusiveMinimum"),
}
