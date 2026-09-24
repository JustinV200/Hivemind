"""Refuse every bee the Hive's own state: its files, its entry points, its loopback and addresses.

ADR-0033, "Bees never touch the Hive's own state", as a floor (ADR-0031): whatever a Warden's or a
Worker's set holds, it is refused `fs:read` and `fs:write` on the Hive's state paths (the database
and its SQLite siblings, the secret store and everything under it, the manifest), `exec` of the
Hive's own entry points (`hive`, and every `hivemind-*` console script, matched on the basename of
argv[0] so `/x/.venv/bin/hive` and `hive.exe` are caught), and `net` to any loopback name or
address, the unspecified address, a link-local address (where cloud metadata services answer),
one of the Hive Stand's own addresses, or one of the names a Cell reaches the Hive Stand by (a
host-gateway alias, a Night Veil Cell's onion service; `HiveState.own_host_names`, refused by
name, and a `*.domain` scope that covers one too). A capability can spell a loopback host many
ways the string grammar never sees through, so when an enforcement point resolved the host first
(`PolicyContext.resolved_addresses`, the HTTP tool), every resolved address is judged too. A path
is compared in `comparable_path` form; a write is also refused on a directory that holds state
(replacing or removing it would take the state with it), and a scope that is itself a glob
pattern is refused whenever its literal prefix could reach state, because a pattern proves
nothing about which paths it will name. This narrows what an injected bee can do with its tools;
it cannot parse every command a bee might run (ADR-0033 records that limit).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.policy.floors`.
    Run by `hivemind.guard.policy.floors.chain` first, before every other floor. Calls into
    `hivemind.guard.capabilities`, `hivemind.guard.net`, this package's `refusal` and the policy
    package's `hive_state`, `models` and `table`.

Key invariants:
    - Applies to bee principals only (a Warden or a Worker): the operator, the Queen and devices
      never act through a tool on a Cell.
    - Pure: judges the needed capability, the context and `GuardPolicy.hive_state`, never the
      held set.
    - Refuses and never allows: an action this floor does not refuse still has to be held.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the decision.
    - hivemind.guard.policy.hive_state for HiveState and comparable_path.
    - hivemind.guard.net for the address predicates.
"""

from __future__ import annotations

import ipaddress
from fnmatch import fnmatchcase

from hivemind.guard.capabilities import Capability, CapabilityFamily
from hivemind.guard.net import (
    IPAddress,
    address_refusal,
    is_loopback_name,
    network_refusal,
    normalise_host,
)
from hivemind.guard.policy.floors.refusal import FloorRefusal, state
from hivemind.guard.policy.hive_state import HiveState, comparable_path
from hivemind.guard.policy.models import PolicyContext, PolicyRequest, PrincipalKind
from hivemind.guard.policy.table import GuardPolicy

STATE_PATHS_FLOOR = "state_paths"  # guard.state_floor.state_paths: the Hive's own files.
ENTRY_POINTS_FLOOR = "entry_points"  # guard.state_floor.entry_points: `hive`, `hivemind-*`.
LOOPBACK_FLOOR = "loopback"  # guard.state_floor.loopback: loopback and the Hive Stand's addresses.
ENTRY_POINT = "hive"  # The operator's CLI: its every subcommand reaches the Hive's stores.
ENTRY_POINT_PREFIX = "hivemind-"  # Every other console script the Hive ships (hivemind-in-cell).
# Launcher suffixes Windows adds to a console script; `hive.exe` is `hive` for this floor.
_LAUNCHER_SUFFIXES = (".exe", ".cmd", ".bat", ".com")
_GLOB_CHARACTERS = frozenset("*?[")  # fnmatch's metacharacters: a scope holding one is a pattern.
_WILDCARD_HOST = "*"  # The `net` scope that reaches every host, loopback included.
_SUBDOMAINS = "*."  # A `net` scope naming every strict subdomain of what follows.
# The bee principals this floor binds: the ones that act through tools on a Cell.
_BEES = frozenset({PrincipalKind.WARDEN, PrincipalKind.WORKER})

__all__ = [
    "ENTRY_POINT",
    "ENTRY_POINTS_FLOOR",
    "ENTRY_POINT_PREFIX",
    "LOOPBACK_FLOOR",
    "STATE_PATHS_FLOOR",
    "hive_state_floor",
]


def hive_state_floor(request: PolicyRequest, policy: GuardPolicy) -> FloorRefusal | None:
    """Refuse a bee any file, entry point or address that is the Hive's own state.

    Args:
        request: The action: its principal, needed capability and context are read.
        policy: The Guard policy; only `hive_state` is read.

    Returns:
        A `guard.state_floor.<floor>` refusal, or None when this floor does not apply.
    """
    if request.principal.kind not in _BEES:
        return None  # Only a bee acts through tools on a Cell; nothing else is bound here.
    needed = request.needed
    # One family, one question: which part of the Hive's state could this action reach?
    if needed.family in (CapabilityFamily.FS_READ, CapabilityFamily.FS_WRITE):
        return _path_refusal(needed, policy.hive_state)
    if needed.family is CapabilityFamily.EXEC:
        return _entry_point_refusal(needed.scope)
    if needed.family is CapabilityFamily.NET:
        return _net_refusal(needed.scope, request.context, policy.hive_state)
    return None


def _path_refusal(needed: Capability, hive: HiveState) -> FloorRefusal | None:
    """Refuse an `fs:read`/`fs:write` scope that names, holds or may match a state path."""
    path = comparable_path(needed.scope)
    writing = needed.family is CapabilityFamily.FS_WRITE
    reaches = (
        _pattern_reaches(path, hive) if _is_pattern(path) else _path_reaches(path, hive, writing)
    )
    if not reaches:
        return None
    return state(
        STATE_PATHS_FLOOR,
        "it names the Hive's own state (its database, secret store or manifest), which no bee "
        "may read or write",
    )


def _path_reaches(path: str, hive: HiveState, writing: bool) -> bool:
    """Decide whether a literal path is a state file, lies in a state directory, or holds one."""
    if path in hive.files or any(_at_or_under(path, root) for root in hive.directories):
        return True
    # A write to a directory holding state could replace or remove the state inside it.
    if writing:
        return any(_at_or_under(held, path) for held in hive.files | hive.directories)
    return False


def _pattern_reaches(pattern: str, hive: HiveState) -> bool:
    """Decide whether a glob pattern could name a state path, conservatively.

    A pattern's literal prefix (everything before its first metacharacter, back to a `/`) is all
    it fixes; if that prefix lies over or under any state path, some name it matches could be
    state, so it is refused, as is any pattern that matches a state path outright.
    """
    first = min(pattern.index(char) for char in _GLOB_CHARACTERS if char in pattern)
    prefix = pattern[:first].rpartition("/")[0]
    candidates = hive.files | hive.directories
    if any(fnmatchcase(held, pattern) for held in candidates):
        return True
    return any(_at_or_under(held, prefix) or _at_or_under(prefix, held) for held in candidates)


def _entry_point_refusal(program: str) -> FloorRefusal | None:
    """Refuse an `exec` whose program (argv[0], by basename) is one of the Hive's entry points."""
    name = comparable_path(program).rpartition("/")[2]
    for suffix in _LAUNCHER_SUFFIXES:
        name = name.removesuffix(suffix)
    # A pattern could name an entry point: judge it against the names it would have to match.
    if _is_pattern(name):
        runs = fnmatchcase(ENTRY_POINT, name) or fnmatchcase(f"{ENTRY_POINT_PREFIX}x", name)
    else:
        runs = name == ENTRY_POINT or name.startswith(ENTRY_POINT_PREFIX)
    if not runs:
        return None
    return state(
        ENTRY_POINTS_FLOOR,
        f"it runs the Hive's own entry point {name!r}, which reaches the Hive's stores and no bee "
        "may run",
    )


def _net_refusal(scope: str, context: PolicyContext, hive: HiveState) -> FloorRefusal | None:
    """Refuse a `net` scope, or any address it resolved to, that reaches the Hive Stand itself."""
    phrase = _scope_phrase(scope, hive.own_addresses) or _own_name_phrase(scope, hive)
    # Resolved addresses are what a connection would really use: each one is judged too.
    if phrase is None:
        phrase = _resolved_phrase(context, hive.own_addresses)
    if phrase is None:
        return None
    return state(LOOPBACK_FLOOR, f"{phrase}, which reaches the Hive Stand itself")


def _resolved_phrase(context: PolicyContext, own: frozenset[IPAddress]) -> str | None:
    """Say which forbidden address the host resolved to, for the first that is one; else None."""
    for address in context.resolved_addresses or ():
        found = address_refusal(address, own)
        if found is not None:
            return f"it resolves to {found}"
    return None


def _scope_phrase(scope: str, own: frozenset[IPAddress]) -> str | None:
    """Say which forbidden host a `net` scope names, as written; None when it names none."""
    host = normalise_host(scope)
    if host == _WILDCARD_HOST:
        return "it reaches any host, loopback included"
    # Every strict subdomain of `localhost` is loopback too (RFC 6761).
    if host.startswith(_SUBDOMAINS):
        return "it names loopback names" if is_loopback_name(host[len(_SUBDOMAINS) :]) else None
    try:
        network = ipaddress.ip_network(host, strict=False)
    except ValueError:
        return "it names a loopback name" if is_loopback_name(host) else None
    found = network_refusal(network, own)
    return f"it names {found}" if found is not None else None


def _own_name_phrase(scope: str, hive: HiveState) -> str | None:
    """Say which of the Hive Stand's own names a `net` scope names or covers; else None."""
    host = normalise_host(scope)
    # `*.domain` covers every name that ends in `.domain`; any other scope names one host.
    covered = host.removeprefix(_SUBDOMAINS) if host.startswith(_SUBDOMAINS) else None
    for name in sorted(hive.own_host_names):
        if name == host or (covered is not None and name.endswith(f".{covered}")):
            return f"it names {name!r}, the Hive Stand's own name from here"
    return None


def _at_or_under(path: str, root: str) -> bool:
    """Return whether `path` is `root` itself or lies somewhere beneath it."""
    return path == root or path.startswith(f"{root.rstrip('/')}/")


def _is_pattern(text: str) -> bool:
    """Return whether `text` holds a glob metacharacter, so it is a pattern and not a name."""
    return any(char in _GLOB_CHARACTERS for char in text)
