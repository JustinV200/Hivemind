"""Define Capability: one grant in one family, written `family` or `family:scope`.

A capability is one thing a bee (or a principal such as an enrolled device) is allowed to do: a
tool it may call, a path it may write, a host it may reach, a model slot it may bind, a Landing
Board route it may use (ADR-0039). `Capability` is that one grant as a frozen value: a
`CapabilityFamily` plus a scope string whose grammar the family's `ScopeKind` fixes. It validates
its own grammar whenever it is built, from `parse` or directly, so an invalid capability can never
exist in memory, and it reads back exactly as written: `str(Capability.parse(s)) == s` for every
accepted string. `matches` says whether holding this capability permits a needed one; it never
matches across families.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Built by every layer that states what
    an action needs (a tool's `net:<host>`, the Capping gate's `fs:write:<path>`) and by
    `hivemind.guard.capabilities.capability_set.CapabilitySet.parse`. Calls into
    `hivemind.guard.capabilities.families` and `.scopes`, and `hivemind.guard.errors`.

Key invariants:
    - A flag family's scope is always empty and its string form is the bare family name; every
      other family's scope is non-empty and valid for its kind (`scopes.scope_error`).
    - `str(Capability.parse(s)) == s` for every string `parse` accepts, and
      `Capability.parse(str(c)) == c` for every Capability that can be constructed.
    - Frozen, extras forbidden and hashable, so a `CapabilitySet` can hold capabilities in a
      frozenset.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for the grammar.
    - hivemind.guard.capabilities.scopes for scope validation and matching per kind.
    - hivemind.guard.capabilities.capability_set for CapabilitySet, the set a bee holds.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.guard.capabilities.families import PARSE_ORDER, CapabilityFamily, ScopeKind
from hivemind.guard.capabilities.scopes import scope_error, scope_matches
from hivemind.guard.errors import InvalidCapabilityError

__all__ = ["Capability"]


class Capability(BaseModel):
    """One grant in one family: a family plus a scope, written `family` or `family:scope`.

    Built directly (`Capability(family=CapabilityFamily.NET, scope="api.example.com")`) or, more
    commonly, from its string form with `Capability.parse`. Either way the grammar is checked:
    direct construction of an invalid capability raises `pydantic.ValidationError`, `parse` of an
    invalid string raises `InvalidCapabilityError`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    family: CapabilityFamily = Field(description="Which kind of grant this is.")
    scope: str = Field(
        default="",
        description="Everything after the family's own prefix and its colon; empty exactly when "
        "the family is a flag (ScopeKind.FLAG), which takes no scope.",
    )

    @model_validator(mode="after")
    def _scope_fits_family(self) -> Capability:
        """Refuse a scope the family's ScopeKind does not accept (or reserves)."""
        error = scope_error(self.family, self.scope)
        if error is not None:
            raise ValueError(f"not a valid {self.family.value} capability: {error}")
        return self

    @classmethod
    def parse(cls, spec: str) -> Capability:
        """Parse a capability string, trying the longest family names first.

        Args:
            spec: The full string, e.g. `"fs:write:/scratch/**"`, `"observe"` or
                `"tool:scope:cell"`. A flag family matches only when `spec` is exactly its name;
                a scoped family matches when `spec` starts with its name and a colon and the rest
                is a valid scope for it, so a scope may itself contain colons (a drive letter).

        Returns:
            The parsed Capability.

        Raises:
            InvalidCapabilityError: No family matches `spec`: an unknown family, a flag with a
                scope (`observe:foo`), a scoped family with no scope (`tool:scope:`), or a scope
                its kind refuses (`honey:clearance:c3`, `llm:nonsense`, `spend:-1`).

        Example:
            >>> Capability.parse("tool:scope:cell").family is CapabilityFamily.TOOL_SCOPE
            True
        """
        first_error: str | None = None
        for family in PARSE_ORDER:
            scope = _scope_in(family, spec)
            if scope is None:
                continue  # This family's name does not fit the front of `spec` at all.
            error = scope_error(family, scope)
            if error is None:
                return cls(family=family, scope=scope)
            # Remember why the longest fitting family refused, the most useful reason to report.
            first_error = first_error or error
        raise InvalidCapabilityError(spec, first_error or _unfit_reason(spec))

    def __str__(self) -> str:
        """Render back to the string `parse` accepts: the bare name for a flag family.

        Returns:
            `"family"` for a flag family, otherwise `"family:scope"`.
        """
        if self.family.scope_kind is ScopeKind.FLAG:
            return self.family.value
        return f"{self.family.value}:{self.scope}"

    def matches(self, needed: Capability) -> bool:
        """Decide whether holding this Capability permits `needed`.

        Args:
            needed: The capability an action requires.

        Returns:
            True when both are in the same family and this scope covers the needed one under the
            family's ScopeKind; always False across families.
        """
        if self.family is not needed.family:
            return False  # A grant in one family never satisfies a need in another.
        return scope_matches(self.family, self.scope, needed.scope)


def _scope_in(family: CapabilityFamily, spec: str) -> str | None:
    """Return the scope `spec` would have under `family`, or None when the name does not fit.

    A flag family fits only the exact string (its scope is then empty); a scoped family fits any
    string that starts with its name and a colon, even one whose remaining scope is empty or
    invalid, so the caller can say why it was refused.
    """
    if family.scope_kind is ScopeKind.FLAG:
        return "" if spec == family.value else None
    prefix = f"{family.value}:"
    return spec[len(prefix) :] if spec.startswith(prefix) else None


def _unfit_reason(spec: str) -> str:
    """Say why no family's name fits `spec`: a flag given a scope, or no such family at all."""
    for family in PARSE_ORDER:
        # `observe:foo` fits no family, but saying the flag takes no scope is the useful answer.
        if family.scope_kind is ScopeKind.FLAG and spec.startswith(f"{family.value}:"):
            return f"{family.value} takes no scope"
    return "no capability family has this name"
