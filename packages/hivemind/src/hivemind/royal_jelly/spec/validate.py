"""Validate a ToolSpec against the rules a well-formed spec can still break.

`ToolSpec` already refuses a malformed field. This module is the second gate, a pure function of
the spec and the requester's own `CapabilitySet` (the set of grants one bee holds): it refuses a
tool that asks for more than its requester may do, that could grant itself other tools, or whose
argument schema would let a caller smuggle extra properties through. It returns every reason found,
not only the first, so a requester fixes a spec in one round trip. Nothing here touches I/O.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.spec. Called by
    the Queen's request path (before a job starts) and by a Warden's own cell-scope path; calls
    into `hivemind.guard` (Capability, CapabilitySet) and the spec model only.

Key invariants:
    - Pure: the same spec and requester always give the same `SpecValidation`.
    - `ok` is True exactly when `reasons` is empty.
    - A capability is covered only when `requester.allows` says so; a spec can never widen a set.
    - No `tool:` capability is ever acceptable in `required_capabilities`: a tool never grants
      itself other tools.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the refusals.
    - hivemind.guard.capabilities.capability_set for `CapabilitySet.allows`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from hivemind.guard import Capability, CapabilitySet, InvalidCapabilityError
from hivemind.royal_jelly.spec.models import ENTRY_POINT_PATTERN, ToolSpec
from hivemind.royal_jelly.spec.package import TOOL_MODULE
from waggle.messages.tool.authoring import ToolScope

# The module half of the entry point must be the one module a package holds (`tool.py`).
_ENTRY_MODULE = TOOL_MODULE.removesuffix(".py")
_ENTRY_RE = re.compile(ENTRY_POINT_PATTERN)  # Compiled once; the pattern is the model's own.
_TOOL_FAMILY_PREFIX = "tool"  # Every capability family that names a tool begins with this.

__all__ = ["SpecValidation", "validate_spec"]


@dataclass(frozen=True, slots=True)
class SpecValidation:
    """The outcome of `validate_spec`: acceptable, or every reason it is not."""

    reasons: tuple[str, ...]  # Empty when the spec is acceptable; one sentence per refusal.

    @property
    def ok(self) -> bool:
        """Return whether the spec is acceptable (no reason was found)."""
        return not self.reasons


def validate_spec(spec: ToolSpec, requester: CapabilitySet) -> SpecValidation:
    """Check `spec` against the requester's own capabilities and the structural rules.

    Args:
        spec: The tool spec to check.
        requester: The capabilities the requesting bee holds right now; the tool may need at most
            these (codingrules section 15: capabilities only attenuate down the tree).

    Returns:
        A `SpecValidation` listing every refusal found, in a fixed order (capabilities, schema,
        entry point, scope).
    """
    reasons = (
        *_capability_reasons(spec, requester),
        *_schema_reasons(spec.input_schema_json),
        *_entry_point_reasons(spec.entry_point),
        *_scope_reasons(spec),
    )
    return SpecValidation(reasons=reasons)


def _capability_reasons(spec: ToolSpec, requester: CapabilitySet) -> list[str]:
    """Refuse unparseable capabilities, `tool:` capabilities and any the requester lacks."""
    reasons: list[str] = []
    # One pass over what the tool asks for: parse first (a typo is a reason, not a crash), then
    # the two semantic checks, in the order a requester would fix them.
    for text in spec.required_capabilities:
        try:
            capability = Capability.parse(text)
        except InvalidCapabilityError as exc:
            reasons.append(f"required capability {text!r} does not parse: {exc}")
            continue
        family = capability.family.value
        # A tool that could hold tool:* would grant itself, or request, other tools.
        if family == _TOOL_FAMILY_PREFIX or family.startswith(f"{_TOOL_FAMILY_PREFIX}:"):
            reasons.append(
                f"required capability {text!r} is a tool capability; a tool never "
                "grants itself other tools"
            )
        elif not requester.allows(capability):
            reasons.append(f"required capability {text!r} is outside the requester's own set")
    return reasons


def _schema_reasons(schema_json: str) -> list[str]:
    """Refuse a schema that is not an object schema closed to extra properties."""
    # The model proved the text is a JSON object once; re-parsing here keeps this function safe
    # for a spec built without validation (`model_construct`) and costs microseconds.
    try:
        schema = json.loads(schema_json)
    except (json.JSONDecodeError, RecursionError):
        return ["input schema is not valid JSON"]
    if not isinstance(schema, dict):
        return ["input schema is not a JSON object"]
    reasons: list[str] = []
    if schema.get("type") != "object":
        reasons.append('input schema must declare "type": "object" at the top level')
    # `is not False` rather than falsy: an absent key means extras are allowed, and so does true.
    if schema.get("additionalProperties") is not False:
        reasons.append('input schema must declare "additionalProperties": false at the top level')
    return reasons


def _entry_point_reasons(entry_point: str) -> list[str]:
    """Refuse an entry point that is malformed or names a module the package does not hold."""
    if _ENTRY_RE.fullmatch(entry_point) is None:
        return [f"entry point {entry_point!r} is not of the form module:attribute"]
    module = entry_point.split(":", 1)[0]
    # A package holds exactly one code module, so any other module could never be loaded.
    if module != _ENTRY_MODULE:
        return [f"entry point module {module!r} must be {_ENTRY_MODULE!r}, the package's module"]
    return []


def _scope_reasons(spec: ToolSpec) -> list[str]:
    """Refuse a scope that does not agree with the spec's Cell, and an empty platform list."""
    reasons: list[str] = []
    if spec.scope is ToolScope.CELL and spec.cell_id is None:
        reasons.append("a CELL-scope tool must name its Cell")
    if spec.scope is ToolScope.HIVE and spec.cell_id is not None:
        reasons.append("a HIVE-scope tool must not name a Cell")
    if not spec.supported_os:
        reasons.append("a tool must support at least one operating system")
    return reasons
