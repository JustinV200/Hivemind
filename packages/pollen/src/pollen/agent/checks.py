"""Match a request's needs against the node's granted capabilities: the pure capability matcher.

The Queen (the Hive's central orchestrator) grants a device a list of capability strings when it
enrols (and may replace the list later with a ``swarm.node_update``), and the Pollen Packet (the
thin gateway program on a device the Hive borrows) checks every request against that list before
any executor runs: a second, independent wall behind the check the Hive Stand already made
(ADR-0051). A string is ``family:scope`` in one of five families the packet understands: ``exec``,
``fs:read``, ``fs:write``, ``cell:outside_scratch`` and ``watch``; any other family, or a string
that does not parse, grants nothing. For the four path-like families the held scope is a glob:
equal as text, or accepted by ``fnmatch`` against the needed scope, case-sensitively, after every
backslash becomes a slash in both (``*`` matches any run of characters, ``/`` included, so ``**``
needs no special case). For ``watch`` the held scope is a prefix. The literal ``{scratch}`` in a
held scope stands for the lease's scratch root, escaped so a root with an unusual name can match
only itself. This is the same matcher the Hive's guard has, written again because pollen may
import nothing from hivemind; the gateway contract suite holds the pair together (gateway-contract
section 10).

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by
    pollen.agent.dispatch after an executor's ``prepare`` and before it runs; calls into
    pollen.executors.base (the family names) and the standard library only.

Key invariants:
    - Pure: no I/O, no clock, no state; the same inputs always give the same answer.
    - An unknown or unparsable capability never widens anything: it is ignored.
    - A denial's message names the family and the request, never the scope (a scope is a path).

See Also:
    - docs/waggle/gateway-contract.md section 10 for the grammar and the matching rules.
    - docs/waggle/enrolment.md section 7.1 for the capabilities an access level grants.
"""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase

from pollen.errors import CapabilityDeniedError
from pollen.executors.base import EXEC, FS_READ, FS_WRITE, OUTSIDE_SCRATCH, WATCH, Need

SCRATCH_PLACEHOLDER = "{scratch}"  # In a held scope: the lease's scratch root.
FAMILIES = (EXEC, FS_READ, FS_WRITE, OUTSIDE_SCRATCH, WATCH)  # The five the packet understands.

__all__ = [
    "FAMILIES",
    "SCRATCH_PLACEHOLDER",
    "check_needs",
    "covers",
    "parse_capability",
    "substitute_scratch",
]


def parse_capability(text: str) -> tuple[str, str] | None:
    """Split a capability string into its family and scope.

    Args:
        text: A string from the node's granted capabilities.

    Returns:
        ``(family, scope)``; None for a family the packet does not understand.
    """
    for family in FAMILIES:
        prefix = f"{family}:"
        if text.startswith(prefix):
            return family, text[len(prefix) :]
    return None


def substitute_scratch(scope: str, scratch_root: str | None) -> str:
    """Replace ``{scratch}`` in a held scope with the lease's escaped scratch root.

    Args:
        scope: A held scope that may contain the token.
        scratch_root: The lease's scratch root; None when the request has no lease.

    Returns:
        The scope with the token replaced; unchanged when there is no root to put in.
    """
    if scratch_root is None or SCRATCH_PLACEHOLDER not in scope:
        return scope
    root = scratch_root.replace("\\", "/").rstrip("/")
    # Glob metacharacters in the root are made literal; "[" goes first so the brackets the other
    # two replacements introduce are not escaped again.
    escaped = root.replace("[", "[[]").replace("*", "[*]").replace("?", "[?]")
    return scope.replace(SCRATCH_PLACEHOLDER, escaped)


def covers(held: Iterable[str], need: Need, scratch_root: str | None) -> bool:
    """Say whether any held capability covers ``need``.

    Args:
        held: The node's granted capability strings.
        need: One capability a request needs.
        scratch_root: The lease's scratch root for the ``{scratch}`` token, or None.

    Returns:
        True if some string in the same family has a scope that covers the needed one.
    """
    for capability in held:
        parsed = parse_capability(capability)
        if parsed is None or parsed[0] != need.family:
            continue
        pattern = substitute_scratch(parsed[1], scratch_root)
        if need.family == WATCH:
            # A watch scope is a prefix: equal, or ending in * and a prefix of the needed one.
            if pattern == need.scope or (
                pattern.endswith("*") and need.scope.startswith(pattern[:-1])
            ):
                return True
            continue
        # Equal as text, or a glob that accepts it, with backslashes read as slashes in both.
        wanted = need.scope.replace("\\", "/")
        glob = pattern.replace("\\", "/")
        if glob == wanted or fnmatchcase(wanted, glob):
            return True
    return False


def check_needs(
    held: Iterable[str], needs: Iterable[Need], scratch_root: str | None, request_id: str
) -> None:
    """Refuse a request unless every need is covered.

    Args:
        held: The node's granted capability strings, as they are now.
        needs: What the request needs.
        scratch_root: The lease's scratch root, or None.
        request_id: The request's envelope id, for the message.

    Raises:
        CapabilityDeniedError: Some need has no covering capability.
    """
    grants = tuple(held)
    for need in needs:
        if not covers(grants, need, scratch_root):
            raise CapabilityDeniedError(
                f"Request {request_id} needs a {need.family} capability this node was not granted."
            )
