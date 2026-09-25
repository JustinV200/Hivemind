"""Validate and match capability scopes, one rule per ScopeKind: pure functions over strings.

Every family in `hivemind.guard.capabilities.families` declares a `ScopeKind`; this module is
what each kind means. `scope_error` says whether a scope is well formed for its family (a flag
takes none; every other kind needs a non-empty scope in its own grammar, and a scope that would
read back as a longer family is reserved), and `scope_matches` says whether a held scope covers a
needed one of the same family. Both are pure and total over valid input, so `Capability` can call
them from its validator and its `matches` without any state of its own. The `net` family's HOST
kind is involved enough to live in `hivemind.guard.capabilities.hosts`; this module dispatches to
it like to any other kind. `glob_literal` makes a path safe to embed in a glob scope: a scratch
root or a keep root that happens to contain `*`, `?` or `[` would otherwise widen the grant built
from it to every sibling the pattern happens to match.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by `hivemind.guard.
    capabilities.capability` (validation, parsing and `matches`). Calls into `.families` and
    `.hosts` only.

Key invariants:
    - A scope `scope_error` accepts for a family never reads back as a different family:
      `f"{family}:{scope}"` would parse as a longer family exactly when that longer family
      shadows it, and such scopes are refused (`tool:request`, `tool:scope:<x>`), which is what
      keeps `Capability.parse(str(capability)) == capability` true for every valid capability.
    - `scope_matches` never widens: for every kind, a held scope covers a needed one only when
      everything the needed scope can name is something the held scope already admits.
    - A spend amount is a plain non-negative decimal (`5`, `5.00`); no sign, exponent, `inf` or
      `nan`, so an amount compares as a number and reads back exactly as written.
    - A glob scope built from a path goes through `glob_literal` first, so the path matches only
      itself; a held glob scope always covers an identical needed one.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for each kind's rule.
    - hivemind.guard.capabilities.families for the family table these rules are keyed on.
    - hivemind.guard.capabilities.hosts for the HOST kind (`net`).
"""

from __future__ import annotations

import fnmatch
import re

from hivemind.guard.capabilities.families import PARSE_ORDER, CapabilityFamily, ScopeKind
from hivemind.guard.capabilities.hosts import host_covers, host_error

WILDCARD = "*"  # Every name of an enumerated family, no spend ceiling, or (glob/prefix) anything.
_AMOUNT = re.compile(r"[0-9]+(\.[0-9]+)?")  # A plain decimal amount: digits, then optional cents.
_GLOB_SPECIALS = frozenset("*?[")  # The characters fnmatch reads as pattern syntax, not text.

__all__ = ["WILDCARD", "glob_literal", "scope_error", "scope_matches"]


def scope_error(family: CapabilityFamily, scope: str) -> str | None:
    """Explain why `scope` is not a valid scope for `family`, or return None when it is.

    Args:
        family: The family the scope would belong to.
        scope: Everything after `family`'s own prefix and its colon; empty for a flag family.

    Returns:
        None when `family` accepts `scope`; otherwise a short phrase saying what is wrong, for
        an error message (never raised here, so a caller chooses the exception type).
    """
    kind = family.scope_kind
    # A flag carries no scope at all: its whole string is the family name.
    if kind is ScopeKind.FLAG:
        return None if scope == "" else f"{family.value} takes no scope"
    if not scope:
        return f"{family.value} needs a scope"
    shadow = _shadowing_family(family, scope)
    if shadow is not None:
        return f"{family.value}:{scope} reads as {shadow.value}; that scope is reserved"
    return _kind_error(family, kind, scope)


def scope_matches(family: CapabilityFamily, held: str, needed: str) -> bool:
    """Decide whether a held scope covers a needed one, both valid scopes of `family`.

    Args:
        family: The family both scopes belong to; its ScopeKind picks the rule.
        held: The scope of the capability a bee holds.
        needed: The scope of the capability an action requires.

    Returns:
        True if holding `family:held` is enough to permit `family:needed`.
    """
    match family.scope_kind:
        case ScopeKind.FLAG:
            return True  # No scope to compare: holding the family is the whole grant.
        case ScopeKind.GLOB:
            # Equal scopes first: a needed scope that is itself a pattern (attenuating one set by
            # another) is compared as text, and an escaped path (`glob_literal`) never matches its
            # own escaped text. fnmatch's "*" already crosses "/", so "**" needs no special case;
            # both sides are POSIX-normalised so a Windows path and a POSIX pattern still meet.
            posix_held, posix_needed = _to_posix(held), _to_posix(needed)
            return posix_held == posix_needed or fnmatch.fnmatchcase(posix_needed, posix_held)
        case ScopeKind.PREFIX:
            return held == needed or (held.endswith(WILDCARD) and needed.startswith(held[:-1]))
        case ScopeKind.HOST:
            return host_covers(held, needed)
        case ScopeKind.ENUMERATED:
            return held in (needed, WILDCARD)
        case ScopeKind.ORDERED:
            return _rank(family, held) >= _rank(family, needed)
        case _:
            # ScopeKind.AMOUNT, the last kind: a spend ceiling covers any amount at or below it.
            return _amount(held) >= _amount(needed)


def _kind_error(family: CapabilityFamily, kind: ScopeKind, scope: str) -> str | None:
    """Check a non-empty, unreserved scope against its kind's own grammar."""
    match kind:
        case ScopeKind.HOST:
            return host_error(scope)
        case ScopeKind.ENUMERATED:
            return _one_of(scope, (*family.scope_values, WILDCARD))
        case ScopeKind.ORDERED:
            return _one_of(scope, family.scope_values)  # No "*": a ladder has a top rung instead.
        case ScopeKind.AMOUNT:
            valid = scope == WILDCARD or _AMOUNT.fullmatch(scope) is not None
            return None if valid else f"{scope!r} is neither * nor a non-negative amount"
        case _:
            return None  # GLOB and PREFIX scopes are open text: any non-empty scope is valid.


def _one_of(scope: str, allowed: tuple[str, ...]) -> str | None:
    """Accept `scope` only when it is one of `allowed`, the closed list of its family."""
    return None if scope in allowed else f"{scope!r} is not one of {', '.join(allowed)}"


def _shadowing_family(family: CapabilityFamily, scope: str) -> CapabilityFamily | None:
    """Return the longer family `family:scope` would parse as, or None when nothing shadows it.

    Only a family whose name is longer than `family`'s can shadow it, because parsing tries the
    longest names first: a flag shadows by being exactly the written string, a scoped family by
    prefixing it with its own name and a colon.
    """
    written = f"{family.value}:{scope}"
    # Only families with longer names are tried before this one, so only they can shadow it.
    longer = [candidate for candidate in PARSE_ORDER if len(candidate.value) > len(family.value)]
    for other in longer:
        if other.scope_kind is ScopeKind.FLAG:
            if written == other.value:
                return other
        elif written.startswith(f"{other.value}:"):
            return other
    return None


def glob_literal(text: str) -> str:
    """Escape `text` so a glob scope built from it matches it literally, never as a pattern.

    Args:
        text: A path (or any text) about to be embedded in a glob scope, e.g. a scratch root.

    Returns:
        `text` with each `*`, `?` and `[` wrapped in a one-character class (`[*]`, `[?]`, `[[]`),
        which fnmatch reads as exactly that character; every other character is unchanged.

    Example:
        >>> glob_literal("/scratch/run[1]*")
        '/scratch/run[[]1][*]'
    """
    return "".join(f"[{char}]" if char in _GLOB_SPECIALS else char for char in text)


def _to_posix(scope: str) -> str:
    """Normalise a glob scope's separators to "/" so Windows and POSIX paths compare equal."""
    return scope.replace("\\", "/")


def _rank(family: CapabilityFamily, scope: str) -> int:
    """Return an ordered scope's rung: its index in the family's lowest-first value list."""
    return family.scope_values.index(scope)


def _amount(scope: str) -> float:
    """Return a spend scope as a number, `*` being unlimited (already validated as either)."""
    return float("inf") if scope == WILDCARD else float(scope)
